package multimodel.metalevel;

import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoCursor;
import org.bson.Document;
import org.neo4j.driver.AuthTokens;
import org.neo4j.driver.Driver;
import org.neo4j.driver.GraphDatabase;
import org.neo4j.driver.Session;
import org.neo4j.driver.SessionConfig;
import org.neo4j.driver.Value;
import org.neo4j.driver.types.Node;
import runner.Config;
import runner.ConnectionRegistry;
import structures.TableSource;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;

/**
 * Materializes each store as one observation set <em>per scope</em>.
 *
 * <p>The existing adapters tabularize a store into a single relation, which forces two wrong
 * answers on collection-valued fields. A Neo4j list property is stringified, so a tag whose
 * vendors are {@code ["EA_Sports"]} is observed as that bracketed literal and can never be included
 * in a vendor name. A document collection explodes only its lexicographically first array and
 * serializes any other, and the exploded container path is emitted as a column of nulls that is
 * vacuously included in everything.</p>
 *
 * <p>Both follow from treating a collection as a value. Under the scope and extractor model it is
 * not: a list-valued field defines a scope whose matches are its elements, and each element
 * contributes one observation. So every list property and every array path becomes its own
 * observation set, named {@code entity@path}, whose columns carry the full path from the entity
 * root. Dropping the suffix recovers the attribute name a reader expects.</p>
 *
 * <p>Fixing extraction rather than any one algorithm means exact, partial and similarity discovery
 * all inherit it. The similarity path reads the same materialized values, and comparing a vendor
 * name to a near-duplicate is only meaningful once the element is the observation rather than the
 * bracketed list containing it.</p>
 */
public final class MetaLevelExporter {

    /** One materialized observation set: a relation name, its scope, and the file holding it. */
    public record Scope(String relation, String scope, Path file, List<String> columns, long rows) {
    }

    private final Config config;
    private final ConnectionRegistry registry;
    private final boolean includeParentReference;

    public MetaLevelExporter(Config config, ConnectionRegistry registry) {
        this(config, registry, false);
    }

    /**
     * @param includeParentReference emit the owning document's {@code _id} beside each array
     *   element. It is export plumbing rather than a member of the admissible extractor family, and
     *   including it during discovery manufactures a true but uninteresting inclusion of the array
     *   scope's copy into the document scope's. Turn it on when materializing a join, where the
     *   reference is the point.
     */
    public MetaLevelExporter(Config config, ConnectionRegistry registry, boolean includeParentReference) {
        this.config = config;
        this.registry = registry;
        this.includeParentReference = includeParentReference;
    }

    public List<Scope> export(TableSource source, Path outDir) throws Exception {
        Files.createDirectories(outDir);
        if (source instanceof TableSource.Neo4jLabel label) {
            return exportGraph(label, outDir);
        }
        if (source instanceof TableSource.Mongo mongo) {
            return exportDocuments(mongo, outDir);
        }
        return exportFlat(source, outDir);
    }

    // -------------------------------------------------------------------------------- relational

    /** Relational and file sources have a single scope; the existing adapter already produces it. */
    private List<Scope> exportFlat(TableSource source, Path outDir) throws Exception {
        try (io.RelationalInput input = io.RelationalInputFactory.open(source, config, registry)) {
            List<String> columns = List.of(input.getHeader());
            Path file = outDir.resolve(fileName(source.displayName()));
            long rows = 0;
            try (CsvWriter writer = new CsvWriter(file, columns)) {
                while (input.hasNext()) {
                    writer.row(List.of(input.next()));
                    rows++;
                }
            }
            return List.of(new Scope(source.displayName(), "base", file, columns, rows));
        }
    }

    // ------------------------------------------------------------------------------------- graph

    private List<Scope> exportGraph(TableSource.Neo4jLabel source, Path outDir) throws Exception {
        ConnectionRegistry.Neo4jConnection conn = registry.getNeo4j(source.connectionName());
        List<Scope> scopes = new ArrayList<>();

        try (Driver driver = GraphDatabase.driver(conn.uri(), AuthTokens.basic(conn.user(), conn.password()));
             Session session = driver.session(source.database() == null || source.database().isEmpty()
                     ? SessionConfig.defaultConfig() : SessionConfig.forDatabase(source.database()))) {

            String label = source.label().replace("`", "``");
            LinkedHashSet<String> scalarNames = new LinkedHashSet<>();
            LinkedHashSet<String> listNames = new LinkedHashSet<>();
            List<Node> nodes = new ArrayList<>();

            var result = session.run("MATCH (n:`" + label + "`) RETURN n");
            while (result.hasNext()) {
                Node node = result.next().get(0).asNode();
                nodes.add(node);
                for (String key : node.keys()) {
                    if (node.get(key).asObject() instanceof List<?>) {
                        listNames.add(key);
                    } else {
                        scalarNames.add(key);
                    }
                }
            }

            List<String> scalarColumns = new ArrayList<>(scalarNames);
            Path base = outDir.resolve(fileName(source.displayName()));
            long rows = 0;
            try (CsvWriter writer = new CsvWriter(base, scalarColumns)) {
                for (Node node : nodes) {
                    List<String> row = new ArrayList<>(scalarColumns.size());
                    for (String column : scalarColumns) {
                        row.add(text(node.get(column)));
                    }
                    writer.row(row);
                    rows++;
                }
            }
            scopes.add(new Scope(source.displayName(), "base", base, scalarColumns, rows));

            // One scope per list property. The node's scalar properties are deliberately not
            // repeated beside each element: doing so would only manufacture inclusions between a
            // key and that same key repeated once per element.
            for (String property : listNames) {
                List<String> columns = List.of(property);
                String relation = source.displayName() + "@" + property;
                Path file = outDir.resolve(fileName(relation));
                long elements = 0;
                try (CsvWriter writer = new CsvWriter(file, columns)) {
                    for (Node node : nodes) {
                        if (!(node.get(property).asObject() instanceof List<?> list)) {
                            continue;
                        }
                        for (Object element : list) {
                            writer.row(List.of(element == null ? config.nullString : String.valueOf(element)));
                            elements++;
                        }
                    }
                }
                scopes.add(new Scope(relation, property, file, columns, elements));
            }
        }
        return scopes;
    }

    /**
     * Materializes every relationship type of a graph store as one observation set.
     *
     * <p>Under the scope model a relationship type is a scope like any other: its matches are the
     * embeddings of the pattern {@code (a)-[r:TYPE]->(b)}, and extractors over it project from the
     * relationship or from either endpoint. Exporting only node labels, as this did, leaves an edge
     * property such as a purchase's brand unobservable, and leaves an edge unable to link the
     * entities it connects.</p>
     *
     * <p>Endpoints are projected through their <em>identifying</em> properties rather than all of
     * them. A property that is unique across the nodes of a label is a key, so projecting it
     * carries exactly the information a relational counterpart of the same relationship would hold
     * in a foreign key column; projecting every endpoint property instead would copy the node
     * scopes into the edge scope and manufacture one structurally guaranteed inclusion per
     * property. Where no property is unique the endpoint's scalars are projected in full, since
     * there is nothing narrower to identify it by.</p>
     */
    public List<Scope> exportRelationships(String connectionName, String database, Path outDir)
            throws Exception {
        Files.createDirectories(outDir);
        ConnectionRegistry.Neo4jConnection conn = registry.getNeo4j(connectionName);
        List<Scope> scopes = new ArrayList<>();

        try (Driver driver = GraphDatabase.driver(conn.uri(), AuthTokens.basic(conn.user(), conn.password()));
             Session session = driver.session(database == null || database.isEmpty()
                     ? SessionConfig.defaultConfig() : SessionConfig.forDatabase(database))) {

            List<String> types = new ArrayList<>();
            var typeResult = session.run("MATCH ()-[r]->() RETURN DISTINCT type(r) AS t ORDER BY t");
            while (typeResult.hasNext()) {
                types.add(typeResult.next().get("t").asString());
            }

            for (String type : types) {
                scopes.add(exportRelationshipType(session, type, outDir));
            }
        }
        return scopes;
    }

    private Scope exportRelationshipType(Session session, String type, Path outDir)
            throws IOException {
        String escaped = type.replace("`", "``");
        LinkedHashSet<String> edgeProperties = new LinkedHashSet<>();
        List<Object[]> edges = new ArrayList<>();          // {source, relationship, target}
        Map<String, Node> sourceNodes = new LinkedHashMap<>();
        Map<String, Node> targetNodes = new LinkedHashMap<>();

        var result = session.run("MATCH (a)-[r:`" + escaped + "`]->(b) RETURN a, r, b");
        while (result.hasNext()) {
            var record = result.next();
            Node source = record.get("a").asNode();
            Node target = record.get("b").asNode();
            var relationship = record.get("r").asRelationship();
            for (String key : relationship.keys()) {
                if (!(relationship.get(key).asObject() instanceof List<?>)) {
                    edgeProperties.add(key);
                }
            }
            sourceNodes.putIfAbsent(source.elementId(), source);
            targetNodes.putIfAbsent(target.elementId(), target);
            edges.add(new Object[]{source, relationship, target});
        }

        List<String> sourceKeys = identifyingProperties(sourceNodes.values());
        List<String> targetKeys = identifyingProperties(targetNodes.values());

        List<String> columns = new ArrayList<>();
        for (String key : sourceKeys) {
            columns.add("source." + key);
        }
        columns.addAll(edgeProperties);
        for (String key : targetKeys) {
            columns.add("target." + key);
        }

        String relation = "neo4j:-[" + type + "]->";
        Path file = outDir.resolve(fileName(relation));
        try (CsvWriter writer = new CsvWriter(file, columns)) {
            for (Object[] edge : edges) {
                Node source = (Node) edge[0];
                var relationship = (org.neo4j.driver.types.Relationship) edge[1];
                Node target = (Node) edge[2];
                List<String> row = new ArrayList<>(columns.size());
                for (String key : sourceKeys) {
                    row.add(text(source.get(key)));
                }
                for (String property : edgeProperties) {
                    row.add(text(relationship.get(property)));
                }
                for (String key : targetKeys) {
                    row.add(text(target.get(key)));
                }
                writer.row(row);
            }
        }
        return new Scope(relation, type, file, columns, edges.size());
    }

    /** Scalar properties that are unique across these nodes, or all scalars when none is. */
    private List<String> identifyingProperties(Collection<Node> nodes) {
        LinkedHashSet<String> scalars = new LinkedHashSet<>();
        for (Node node : nodes) {
            for (String key : node.keys()) {
                if (!(node.get(key).asObject() instanceof List<?>)) {
                    scalars.add(key);
                }
            }
        }
        List<String> unique = new ArrayList<>();
        for (String key : scalars) {
            LinkedHashSet<String> seen = new LinkedHashSet<>();
            boolean distinct = true;
            for (Node node : nodes) {
                if (!seen.add(text(node.get(key)))) {
                    distinct = false;
                    break;
                }
            }
            if (distinct) {
                unique.add(key);
            }
        }
        return unique.isEmpty() ? new ArrayList<>(scalars) : unique;
    }

    // ---------------------------------------------------------------------------------- document

    private List<Scope> exportDocuments(TableSource.Mongo source, Path outDir) throws Exception {
        ConnectionRegistry.MongoConnection conn = registry.getMongo(source.connectionName());
        List<Scope> scopes = new ArrayList<>();

        try (MongoClient client = MongoClients.create(conn.uri())) {
            var collection = client.getDatabase(source.database()).getCollection(source.collection());

            // Establish the scope structure first: which leaves sit at document level, which paths
            // are arrays, and what fields each array's elements carry. Every array is a scope, not
            // only the first one encountered.
            LinkedHashSet<String> documentPaths = new LinkedHashSet<>();
            LinkedHashSet<String> arrayPaths = new LinkedHashSet<>();
            Map<String, LinkedHashSet<String>> arrayFields = new LinkedHashMap<>();
            try (MongoCursor<Document> cursor = collection.find().iterator()) {
                while (cursor.hasNext()) {
                    describe(cursor.next(), "", documentPaths, arrayPaths, arrayFields);
                }
            }

            List<String> documentColumns = new ArrayList<>(documentPaths);
            Path base = outDir.resolve(fileName(source.displayName()));
            long rows = 0;
            try (CsvWriter writer = new CsvWriter(base, documentColumns);
                 MongoCursor<Document> cursor = collection.find().iterator()) {
                while (cursor.hasNext()) {
                    Map<String, String> flat = new LinkedHashMap<>();
                    flattenScalars(cursor.next(), "", flat);
                    List<String> row = new ArrayList<>(documentColumns.size());
                    for (String column : documentColumns) {
                        row.add(flat.getOrDefault(column, config.nullString));
                    }
                    writer.row(row);
                    rows++;
                }
            }
            scopes.add(new Scope(source.displayName(), "document", base, documentColumns, rows));

            for (String arrayPath : arrayPaths) {
                List<String> columns = new ArrayList<>();
                if (includeParentReference) {
                    columns.add("_id");
                }
                columns.addAll(arrayFields.getOrDefault(arrayPath, new LinkedHashSet<>()));
                String relation = source.displayName() + "@" + arrayPath;
                Path file = outDir.resolve(fileName(relation));
                long elements = 0;
                try (CsvWriter writer = new CsvWriter(file, columns);
                     MongoCursor<Document> cursor = collection.find().iterator()) {
                    while (cursor.hasNext()) {
                        Document document = cursor.next();
                        String id = String.valueOf(document.get("_id"));
                        for (Map<String, String> element : elementsOf(document, arrayPath)) {
                            List<String> row = new ArrayList<>(columns.size());
                            int first = 0;
                            if (includeParentReference) {
                                row.add(id);
                                first = 1;
                            }
                            for (int i = first; i < columns.size(); i++) {
                                row.add(element.getOrDefault(columns.get(i), config.nullString));
                            }
                            writer.row(row);
                            elements++;
                        }
                    }
                }
                scopes.add(new Scope(relation, arrayPath, file, columns, elements));
            }
        }
        return scopes;
    }

    /** Records document-level leaves, array paths, and the fields each array's elements carry. */
    private void describe(Document document, String prefix, LinkedHashSet<String> documentPaths,
                          LinkedHashSet<String> arrayPaths, Map<String, LinkedHashSet<String>> arrayFields) {
        for (Map.Entry<String, Object> entry : document.entrySet()) {
            String path = prefix.isEmpty() ? entry.getKey() : prefix + "." + entry.getKey();
            Object value = entry.getValue();
            if (value instanceof Document nested) {
                describe(nested, path, documentPaths, arrayPaths, arrayFields);
            } else if (value instanceof List<?> list) {
                arrayPaths.add(path);
                LinkedHashSet<String> fields = arrayFields.computeIfAbsent(path, key -> new LinkedHashSet<>());
                for (Object element : list) {
                    if (element instanceof Document elementDocument) {
                        Map<String, String> flat = new LinkedHashMap<>();
                        flattenScalars(elementDocument, path, flat);
                        fields.addAll(flat.keySet());
                    } else {
                        fields.add(path);   // array of scalars: the element itself is the observation
                    }
                }
            } else {
                documentPaths.add(path);
            }
        }
    }

    /** Scalar leaves only. Arrays are not flattened here; they are separate scopes. */
    private void flattenScalars(Document document, String prefix, Map<String, String> out) {
        for (Map.Entry<String, Object> entry : document.entrySet()) {
            String path = prefix.isEmpty() ? entry.getKey() : prefix + "." + entry.getKey();
            Object value = entry.getValue();
            if (value instanceof Document nested) {
                flattenScalars(nested, path, out);
            } else if (!(value instanceof List<?>)) {
                out.put(path, value == null ? config.nullString : String.valueOf(value));
            }
        }
    }

    private List<Map<String, String>> elementsOf(Document document, String arrayPath) {
        Object value = document;
        for (String step : arrayPath.split("[.]")) {
            if (!(value instanceof Document current)) {
                return List.of();
            }
            value = current.get(step);
        }
        if (!(value instanceof List<?> list)) {
            return List.of();
        }
        List<Map<String, String>> elements = new ArrayList<>(list.size());
        for (Object element : list) {
            Map<String, String> flat = new LinkedHashMap<>();
            if (element instanceof Document elementDocument) {
                flattenScalars(elementDocument, arrayPath, flat);
            } else if (element != null) {
                flat.put(arrayPath, String.valueOf(element));
            }
            elements.add(flat);
        }
        return elements;
    }

    // ----------------------------------------------------------------------------------- helpers

    private String text(Value value) {
        return (value == null || value.isNull()) ? config.nullString : String.valueOf(value.asObject());
    }

    private static String fileName(String relation) {
        return relation.replaceAll("[^A-Za-z0-9._@-]", "_") + ".csv";
    }

    /** Minimal RFC 4180 writer; exports are read back by the standard CSV adapter. */
    private static final class CsvWriter implements AutoCloseable {

        private final BufferedWriter out;

        CsvWriter(Path file, List<String> columns) throws IOException {
            this.out = Files.newBufferedWriter(file, StandardCharsets.UTF_8);
            row(columns);
        }

        void row(List<String> values) throws IOException {
            for (int i = 0; i < values.size(); i++) {
                if (i > 0) {
                    out.write(',');
                }
                String value = values.get(i) == null ? "" : values.get(i);
                if (value.indexOf(',') >= 0 || value.indexOf('"') >= 0 || value.indexOf('\n') >= 0) {
                    out.write('"');
                    out.write(value.replace("\"", "\"\""));
                    out.write('"');
                } else {
                    out.write(value);
                }
            }
            out.newLine();
        }

        @Override
        public void close() throws IOException {
            out.close();
        }
    }
}
