import multimodel.adapters.MaskedScope;
import runner.ConnectionRegistry;
import com.mongodb.client.*;
import org.bson.Document;
import org.neo4j.driver.*;
import java.nio.file.*;
import java.io.*;
import java.sql.*;
import java.util.*;

/** Independent native-to-relational conversion. Does not call MetaLevelExporter.
 * Parent and occurrence IDs are retained in lineage relations, outside comparable views.
 * Graph edges retain every scalar endpoint property; comparison projects the shared family.
 */
public final class RelationalConversionBaseline {
    final Path out;
    final ConnectionRegistry registry=ConnectionRegistry.loadDefault();
    final List<Document> catalog=new ArrayList<>();
    RelationalConversionBaseline(Path out) throws IOException { this.out=out; Files.createDirectories(out); }
    static String file(String relation) { return relation.replaceAll("[^A-Za-z0-9._@-]","_")+".mscope"; }
    static String quote(String name) { return "\""+name.replace("\"","\"\"")+"\""; }
    static String scalar(Object value) { return value==null?null:String.valueOf(value); }
    void register(String relation,String scope,List<String> columns,long count) {
        catalog.add(new Document("relation",relation).append("scope",scope).append("rows",count)
                .append("columns",columns.size()).append("file",file(relation)));
    }
    void postgres(String connection,String schema,String table) throws Exception {
        var c=registry.getPostgres(connection);
        try(Connection db=DriverManager.getConnection(c.url(),c.user(),c.password())) {
            db.setAutoCommit(false);
            try(Statement st=db.createStatement()) {
                st.setFetchSize(1000);
                try(ResultSet rs=st.executeQuery("SELECT * FROM "+quote(schema)+"."+quote(table))) {
                    var meta=rs.getMetaData(); List<String> columns=new ArrayList<>();
                    for(int i=1;i<=meta.getColumnCount();i++) columns.add(meta.getColumnName(i));
                    String relation=schema+"."+table; long count=0;
                    try(var writer=new MaskedScope.Writer(out.resolve(file(relation)),columns)) {
                        while(rs.next()) {
                            List<String> values=new ArrayList<>();
                            // Text of the JDBC typed value; getString() renders FLOAT 44.0 as "44"
                            // in PostgreSQL.
                            for(int i=1;i<=columns.size();i++) values.add(scalar(rs.getObject(i)));
                            writer.row(values); count++;
                        }
                    }
                    register(relation,"base",columns,count);
                }
            }
        }
    }
    // Spool normalized tuples so the conversion does not require the whole collection in memory.
    final class Relation implements AutoCloseable {
        final String name,scope; final Path spool; final BufferedWriter data,links;
        final Set<String> fields=new TreeSet<>(); long count;
        Relation(String name,String scope) throws IOException {
            this.name=name; this.scope=scope; spool=out.resolve(file(name)+".tuples.jsonl");
            data=Files.newBufferedWriter(spool);
            links=Files.newBufferedWriter(out.resolve(file(name)+".lineage.jsonl"));
        }
        void row(Map<String,String> values,String parent,String occurrence) throws IOException {
            fields.addAll(values.keySet());
            Document payload=new Document(); values.forEach(payload::append);
            data.write(payload.toJson()); data.newLine();
            links.write(new Document("row",count++).append("parent",parent).append("occurrence",occurrence).toJson());
            links.newLine();
        }
        public void close() throws IOException {
            data.close(); links.close();
            List<String> columns=new ArrayList<>(fields);
            try(var reader=Files.newBufferedReader(spool);
                var writer=new MaskedScope.Writer(out.resolve(file(name)),columns)) {
                String line;
                while((line=reader.readLine())!=null) {
                    Document doc=Document.parse(line); List<String> values=new ArrayList<>();
                    for(String column:columns) values.add(doc.getString(column));
                    writer.row(values);
                }
            }
            Files.delete(spool);
            register(name,scope,columns,count);
        }
    }
    void flatten(Map<String,Object> value,String prefix,Map<String,String> leaves) {
        value.forEach((key,v)->{
            String path=prefix.isEmpty()?key:prefix+"."+key;
            if(v instanceof Document d) flatten(d,path,leaves);
            else if(!(v instanceof List<?>)) leaves.put(path,scalar(v));
        });
    }
    void arrays(Map<String,Object> doc,String prefix,String root,String parent,
                Map<String,Relation> children) throws IOException {
        for(var entry:doc.entrySet()) {
            String path=prefix.isEmpty()?entry.getKey():prefix+"."+entry.getKey();
            if(entry.getValue() instanceof Document d) arrays(d,path,root,parent,children);
            else if(entry.getValue() instanceof List<?> list) {
                Relation child=children.get(path);
                if(child==null) { child=new Relation(root+"@"+path,path); children.put(path,child); }
                for(int i=0;i<list.size();i++) {
                    Object item=list.get(i); Map<String,String> leaves=new TreeMap<>();
                    if(item instanceof Document d) {
                        flatten(d,path,leaves);
                        arrays(d,path,root,parent+"/"+path+"["+i+"]",children);
                    } else leaves.put(path,scalar(item));
                    child.row(leaves,parent,Integer.toString(i));
                }
            }
        }
    }
    void mongo(String connection,String database,String collection) throws Exception {
        try(MongoClient client=MongoClients.create(registry.getMongo(connection).uri())) {
            String root=database+"."+collection;
            Map<String,Relation> children=new LinkedHashMap<>();
            try(Relation base=new Relation(root,"base");
                var cursor=client.getDatabase(database).getCollection(collection).find().iterator()) {
                long ordinal=0;
                while(cursor.hasNext()) {
                    Document doc=cursor.next(); Map<String,String> leaves=new TreeMap<>();
                    flatten(doc,"",leaves);
                    String id=String.valueOf(ordinal++);
                    base.row(leaves,null,id); arrays(doc,"",root,id,children);
                }
            } finally { for(Relation child:children.values()) child.close(); }
        }
    }
    static Map<String,String> scalars(org.neo4j.driver.types.Entity entity,String prefix) {
        Map<String,String> values=new TreeMap<>();
        for(String key:entity.keys()) {
            Object value=entity.get(key).asObject();
            if(!(value instanceof List<?>)) values.put(prefix+key,scalar(value));
        }
        return values;
    }
    void union(String name,String memberList,String columnList) throws IOException {
        List<String> columns=List.of(columnList.split(",")); long count=0;
        try(var writer=new MaskedScope.Writer(out.resolve(file(name)),columns)) {
            for(String member:memberList.split(",")) {
                if(catalog.stream().noneMatch(e->member.equals(e.getString("relation"))))
                    throw new IOException("Unknown union member: "+member);
                try(var reader=new MaskedScope.Reader(out.resolve(file(member)))) {
                    String[] row;
                    while((row=reader.next())!=null) {
                        List<String> values=new ArrayList<>();
                        for(String c:columns) {
                            int index=reader.columns.indexOf(c); values.add(index<0?null:row[index]);
                        }
                        writer.row(values); count++;
                    }
                }
            }
        }
        register(name,"union",columns,count);
        catalog.getLast().append("members",List.of(memberList.split(",")));
    }
    void graph(String connection,String database,String name,boolean edges) throws Exception {
        var c=registry.getNeo4j(connection);
        try(org.neo4j.driver.Driver driver=GraphDatabase.driver(c.uri(),AuthTokens.basic(c.user(),c.password()));
            Session session=driver.session(SessionConfig.forDatabase(database))) {
            if(edges) {
                var types=session.run("MATCH ()-[r]->() RETURN DISTINCT type(r) AS t").list();
                for(var type:types) {
                    String t=type.get("t").asString();
                    if(!name.equals("*")&&!t.startsWith(name)) continue;
                    try(Relation relation=new Relation("neo4j:-["+t+"]->",t)) {
                        var cursor=session.run("MATCH (a)-[r:`"+t.replace("`","``")+"`]->(b) RETURN a,r,b");
                        while(cursor.hasNext()) {
                            var row=cursor.next(); var a=row.get("a").asNode(); var b=row.get("b").asNode();
                            var edge=row.get("r").asRelationship();
                            var values=scalars(a,"source."); values.putAll(scalars(edge,"")); values.putAll(scalars(b,"target."));
                            relation.row(values,a.elementId()+"->"+b.elementId(),edge.elementId());
                        }
                    }
                }
            } else {
                String root="neo4j:"+name; Map<String,Relation> children=new LinkedHashMap<>();
                try(Relation base=new Relation(root,"base")) {
                    var cursor=session.run("MATCH (n:`"+name.replace("`","``")+"`) RETURN n");
                    while(cursor.hasNext()) {
                        var node=cursor.next().get(0).asNode(); base.row(scalars(node,""),null,node.elementId());
                        for(String key:node.keys()) if(node.get(key).asObject() instanceof List<?> list) {
                            Relation child=children.get(key);
                            if(child==null) { child=new Relation(root+"@"+key,key); children.put(key,child); }
                            for(int i=0;i<list.size();i++) {
                                Map<String,String> value=new HashMap<>(); value.put(key,scalar(list.get(i)));
                                child.row(value,node.elementId(),Integer.toString(i));
                            }
                        }
                    }
                } finally { for(Relation child:children.values()) child.close(); }
            }
        }
    }
    public static void main(String[] args) throws Exception {
        var converter=new RelationalConversionBaseline(Path.of(args[1]));
        for(String raw:Files.readAllLines(Path.of(args[0]))) {
            String line=raw.split("#",2)[0].trim(); if(line.isEmpty()) continue;
            String[] p=line.split("\\s+");
            switch(p[0]) {
                case "postgres" -> converter.postgres(p[1],p[2],p[3]);
                case "mongo" -> converter.mongo(p[1],p[2],p[3]);
                case "neo4j-label" -> converter.graph(p[1],p[2],p[3],false);
                case "neo4j-rels" -> converter.graph(p[1],p[2],p[3],true);
                case "union" -> converter.union(p[1],p[2],p[3]);
                default -> throw new IllegalArgumentException("Unsupported baseline source: "+p[0]);
            }
        }
        Files.writeString(converter.out.resolve("catalog.json"),"["+String.join(",\n",converter.catalog.stream().map(Document::toJson).toList())+"]");
        System.out.println("Converted "+converter.catalog.size()+" relational tables");
    }
}
