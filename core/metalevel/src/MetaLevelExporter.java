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
import multimodel.adapters.MaskedScope;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.function.Predicate;

/**
 * Materializes each store as one observation set <em>per scope</em>.
 *
 * <p>A list-valued field defines a scope whose matches are its elements, each contributing one
 * observation. So every list property and every array path becomes its own observation set, named
 * {@code entity@path}, whose columns carry the full path from the entity root.</p>
 */
public final class MetaLevelExporter {

    /** One materialized observation set: a relation name, its scope, and the file holding it. */
    public record Scope(String relation, String scope, Path file, List<String> columns, long rows,
                        TypeProfile types, List<String> members) {
        public Scope(String relation, String scope, Path file, List<String> columns, long rows) {
            this(relation, scope, file, columns, rows, new TypeProfile(), List.of());
        }

        public Scope(String relation, String scope, Path file, List<String> columns, long rows,
                     TypeProfile types) {
            this(relation, scope, file, columns, rows, types, List.of());
        }
    }

    private final Config config;
    private final ConnectionRegistry registry;
    private final boolean includeParentReference;
    private final boolean definedness = Boolean.getBoolean("mm.definedness");

    public MetaLevelExporter(Config config, ConnectionRegistry registry) {
        this(config, registry, false);
    }

    /**
     * @param includeParentReference emit the owning document's {@code _id} beside each array
     *   element. Leave it off during discovery, where it only yields a trivial inclusion into the
     *   document scope; turn it on when materializing a join.
     */
    public MetaLevelExporter(Config config, ConnectionRegistry registry, boolean includeParentReference) {
        this.config = config;
        // With definedness masks, native nulls are preserved until the mask is written, so
        // missingness is never inferred from a string value.
        if (definedness) this.config.nullString = null;
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

    /** Relational and file sources have a single scope, produced by the standard adapter. */
    private List<Scope> exportFlat(TableSource source, Path outDir) throws Exception {
        if (definedness && source instanceof TableSource.File)
            throw new IllegalArgumentException("Cannot infer definedness from legacy CSV; regenerate from native sources");
        try (io.RelationalInput input = io.RelationalInputFactory.open(source, config, registry)) {
            List<String> columns = List.of(input.getHeader());
            Path file = outDir.resolve(fileName(source.displayName()));
            long rows = 0;
            try (CsvWriter writer = new CsvWriter(file, columns)) {
                while (input.hasNext()) {
                    writer.row(java.util.Arrays.asList(input.next()));
                    rows++;
                }
            }
            return List.of(new Scope(source.displayName(), "base", file, columns, rows,
                    declaredColumnTypes(source, columns, rows)));
        }
    }

    /**
     * Column types as declared in {@code information_schema}, counted once per row so the histogram
     * has the same shape as for document and graph stores.
     */
    private TypeProfile declaredColumnTypes(TableSource source, List<String> columns, long rows) {
        TypeProfile types = new TypeProfile();
        if (!(source instanceof TableSource.Postgres postgres)) {
            return types;
        }
        ConnectionRegistry.PostgresConnection conn = registry.getPostgres(postgres.connectionName());
        String sql = "SELECT column_name, data_type FROM information_schema.columns"
                + " WHERE table_schema = ? AND table_name = ?";
        try (java.sql.Connection connection = java.sql.DriverManager.getConnection(
                     conn.url(), conn.user(), conn.password());
             java.sql.PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, postgres.schema());
            statement.setString(2, postgres.table());
            try (java.sql.ResultSet result = statement.executeQuery()) {
                while (result.next()) {
                    String column = result.getString(1);
                    if (columns.contains(column)) {
                        types.record(column, result.getString(2), rows);
                    }
                }
            }
        } catch (java.sql.SQLException e) {
            System.err.println("No declared types for " + source.displayName() + ": " + e.getMessage());
        }
        return types;
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
            TypeProfile nodeTypes = new TypeProfile();
            try (CsvWriter writer = new CsvWriter(base, scalarColumns)) {
                for (Node node : nodes) {
                    List<String> row = new ArrayList<>(scalarColumns.size());
                    for (String column : scalarColumns) {
                        row.add(text(node.get(column)));
                        nodeTypes.record(column, typeName(node.get(column)));
                    }
                    writer.row(row);
                    rows++;
                }
            }
            scopes.add(new Scope(source.displayName(), "base", base, scalarColumns, rows,
                    nodeTypes));

            // One scope per list property. The node's scalars are not repeated beside each
            // element, which would only produce trivial inclusions of a key into itself.
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
                            writer.row(java.util.Collections.singletonList(element == null ? config.nullString : String.valueOf(element)));
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
     * <p>A relationship type is a scope whose matches are the embeddings of
     * {@code (a)-[r:TYPE]->(b)}; extractors project from the relationship or from either endpoint.</p>
     *
     * <p>Endpoints are projected through their identifying properties only, like a foreign key
     * column; projecting every endpoint property would copy the node scopes into the edge scope.
     * See {@link #identifyingProperties}.</p>
     */
    public List<Scope> exportRelationships(String connectionName, String database, Path outDir)
            throws Exception {
        return exportRelationships(connectionName, database, outDir, type -> true);
    }

    // ------------------------------------------------------------------------------ union scopes

    /**
     * Builds a union scope: one observation set whose matches are those of several others.
     *
     * <p>The union is constructed from already exported scopes rather than read from a store. It
     * captures references into a population split across scopes, e.g. MMSBench-Net's six device
     * labels, which no single-scope or n-ary IND can express.</p>
     *
     * <p>The semantics are a <em>tagged</em> union: rows are concatenated and multiplicities add,
     * which is deterministic and needs no cross-model notion of object identity. For disjoint
     * members it coincides with set union.</p>
     *
     * <p>Members are named by the caller; enumerating unions would be exponential.</p>
     *
     * @param relation the name the union scope is published under
     * @param columns  the extractors the union carries; a member lacking one contributes empty
     * @param members  the already-exported scopes whose matches are unioned
     */
    public Scope exportUnion(String relation, List<String> columns, List<Scope> members,
                             Path outDir) throws IOException {
        Files.createDirectories(outDir);
        Path file = outDir.resolve(relation.replace(':', '_') + (definedness ? ".mscope" : ".csv"));
        long rows = 0;
        try (CsvWriter writer = new CsvWriter(file, columns)) {
            for (Scope member : members) {
                if (definedness) {
                    try (MaskedScope.Reader reader = new MaskedScope.Reader(member.file())) {
                        int[] positions = columns.stream().mapToInt(reader.columns::indexOf).toArray();
                        String[] cells;
                        while ((cells = reader.next()) != null) {
                            List<String> row = new ArrayList<>();
                            for (int index : positions) row.add(index < 0 ? null : cells[index]);
                            writer.row(row);
                            rows++;
                        }
                    }
                    continue;
                }
                List<String> header;
                try (var lines = Files.lines(member.file(), StandardCharsets.UTF_8)) {
                    header = lines.findFirst().map(MetaLevelExporter::splitCsv)
                            .orElse(List.of());
                }
                // Positions are resolved per member: members need not agree on column order, and
                // one may lack a given extractor.
                int[] position = new int[columns.size()];
                for (int i = 0; i < columns.size(); i++) {
                    position[i] = header.indexOf(columns.get(i));
                }
                try (var lines = Files.lines(member.file(), StandardCharsets.UTF_8)) {
                    var iterator = lines.iterator();
                    if (iterator.hasNext()) {
                        iterator.next();  // header
                    }
                    while (iterator.hasNext()) {
                        List<String> cells = splitCsv(iterator.next());
                        List<String> row = new ArrayList<>(columns.size());
                        for (int index : position) {
                            row.add(index >= 0 && index < cells.size() ? cells.get(index) : "");
                        }
                        writer.row(row);
                        rows++;
                    }
                }
            }
        }
        // Members are recorded in the catalog so that downstream steps can recognise a member's
        // inclusion in its union as entailed by construction rather than discovered.
        return new Scope(relation, "union", file, columns, rows, new TypeProfile(),
                members.stream().map(Scope::relation).toList());
    }

    /** Splits one RFC 4180 line as written by {@link CsvWriter}. */
    private static List<String> splitCsv(String line) {
        List<String> cells = new ArrayList<>();
        StringBuilder cell = new StringBuilder();
        boolean quoted = false;
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (quoted) {
                if (c == '"') {
                    if (i + 1 < line.length() && line.charAt(i + 1) == '"') {
                        cell.append('"');
                        i++;
                    } else {
                        quoted = false;
                    }
                } else {
                    cell.append(c);
                }
            } else if (c == '"') {
                quoted = true;
            } else if (c == ',') {
                cells.add(cell.toString());
                cell.setLength(0);
            } else {
                cell.append(c);
            }
        }
        cells.add(cell.toString());
        return cells;
    }

    /**
     * Like {@link #exportRelationships(String, String, Path)}, restricted to the relationship types
     * {@code keep} accepts. One graph store may hold several corpora, and relationship types are not
     * namespaced by node label.
     */
    public List<Scope> exportRelationships(String connectionName, String database, Path outDir,
                                           Predicate<String> keep) throws Exception {
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
                if (keep.test(type)) {
                    scopes.add(exportRelationshipType(session, type, outDir));
                }
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

        Map<String, List<String>> declared = declaredKeys(session);
        List<String> sourceKeys = identifyingProperties(sourceNodes.values(), declared);
        List<String> targetKeys = identifyingProperties(targetNodes.values(), declared);

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

    /** Uniqueness and node-key constraints the graph declares, as label -> properties. */
    private Map<String, List<String>> declaredKeys(Session session) {
        Map<String, List<String>> byLabel = new LinkedHashMap<>();
        try {
            // Constraint types are matched by substring because their names differ between
            // versions: Neo4j 4 reports UNIQUENESS and NODE_KEY, Neo4j 5 NODE_PROPERTY_UNIQUENESS.
            var result = session.run("SHOW CONSTRAINTS YIELD type, entityType, labelsOrTypes, "
                    + "properties RETURN type, entityType, labelsOrTypes, properties");
            while (result.hasNext()) {
                var record = result.next();
                String type = record.get("type").asString("").toUpperCase(Locale.ROOT);
                String entity = record.get("entityType").asString("NODE").toUpperCase(Locale.ROOT);
                if (!entity.startsWith("NODE")
                        || !(type.contains("UNIQUE") || type.contains("KEY"))) {
                    continue;
                }
                if (record.get("labelsOrTypes").isNull() || record.get("properties").isNull()) {
                    continue;
                }
                for (Object label : record.get("labelsOrTypes").asList()) {
                    List<String> properties = new ArrayList<>();
                    for (Object property : record.get("properties").asList()) {
                        properties.add(String.valueOf(property));
                    }
                    byLabel.computeIfAbsent(String.valueOf(label), k -> new ArrayList<>())
                            .addAll(properties);
                }
            }
        } catch (RuntimeException e) {
            // SHOW CONSTRAINTS is unavailable on older servers and some managed deployments; fall
            // back to instance-level keys.
            return byLabel;
        }
        return byLabel;
    }

    /**
     * The properties an endpoint is projected through, preferring what the schema declares.
     *
     * In order: properties with a declared uniqueness or node-key constraint on any of the nodes'
     * labels; otherwise instance-level candidate keys (present on all nodes and distinct across
     * them), which may be accidental; otherwise every scalar.
     */
    private List<String> identifyingProperties(Collection<Node> nodes,
                                               Map<String, List<String>> declared) {
        LinkedHashSet<String> scalars = new LinkedHashSet<>();
        LinkedHashSet<String> labels = new LinkedHashSet<>();
        for (Node node : nodes) {
            node.labels().forEach(labels::add);
            for (String key : node.keys()) {
                if (!(node.get(key).asObject() instanceof List<?>)) {
                    scalars.add(key);
                }
            }
        }

        List<String> fromSchema = new ArrayList<>();
        for (String label : labels) {
            for (String key : declared.getOrDefault(label, List.of())) {
                if (scalars.contains(key) && !fromSchema.contains(key)) {
                    fromSchema.add(key);
                }
            }
        }
        if (!fromSchema.isEmpty()) {
            return fromSchema;
        }

        List<String> candidates = new ArrayList<>();
        for (String key : scalars) {
            LinkedHashSet<String> seen = new LinkedHashSet<>();
            boolean distinct = true;
            boolean present = true;
            for (Node node : nodes) {
                Value value = node.get(key);
                if (value == null || value.isNull()) {
                    present = false;
                    break;
                }
                if (!seen.add(text(value))) {
                    distinct = false;
                    break;
                }
            }
            if (distinct && present) {
                candidates.add(key);
            }
        }
        return candidates.isEmpty() ? new ArrayList<>(scalars) : candidates;
    }

    // ---------------------------------------------------------------------------------- document

    private List<Scope> exportDocuments(TableSource.Mongo source, Path outDir) throws Exception {
        ConnectionRegistry.MongoConnection conn = registry.getMongo(source.connectionName());
        List<Scope> scopes = new ArrayList<>();

        try (MongoClient client = MongoClients.create(conn.uri())) {
            var collection = client.getDatabase(source.database()).getCollection(source.collection());

            // First pass: which leaves sit at document level, which paths are arrays, and what
            // fields each array's elements carry. Every array is its own scope.
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
            TypeProfile documentTypes = new TypeProfile();
            try (CsvWriter writer = new CsvWriter(base, documentColumns);
                 MongoCursor<Document> cursor = collection.find().iterator()) {
                while (cursor.hasNext()) {
                    Map<String, String> flat = new LinkedHashMap<>();
                    flattenScalars(cursor.next(), "", flat, documentTypes);
                    List<String> row = new ArrayList<>(documentColumns.size());
                    for (String column : documentColumns) {
                        row.add(flat.getOrDefault(column, config.nullString));
                    }
                    writer.row(row);
                    rows++;
                }
            }
            scopes.add(new Scope(source.displayName(), "document", base, documentColumns, rows,
                    documentTypes));

            for (String arrayPath : arrayPaths) {
                List<String> columns = new ArrayList<>();
                if (includeParentReference) {
                    columns.add("_id");
                }
                columns.addAll(arrayFields.getOrDefault(arrayPath, new LinkedHashSet<>()));
                String relation = source.displayName() + "@" + arrayPath;
                Path file = outDir.resolve(fileName(relation));
                long elements = 0;
                TypeProfile elementTypes = new TypeProfile();
                try (CsvWriter writer = new CsvWriter(file, columns);
                     MongoCursor<Document> cursor = collection.find().iterator()) {
                    while (cursor.hasNext()) {
                        Document document = cursor.next();
                        String id = String.valueOf(document.get("_id"));
                        for (Map<String, String> element
                                : elementsOf(document, arrayPath, elementTypes)) {
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
                // An array path whose every occurrence is empty yields no observations and would be
                // vacuously included in every extractor, so it is dropped.
                if (columns.isEmpty() || elements == 0) {
                    Files.deleteIfExists(file);
                    continue;
                }
                scopes.add(new Scope(relation, arrayPath, file, columns, elements, elementTypes));
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
        flattenScalars(document, prefix, out, null);
    }

    /** As above, additionally recording the BSON type each value arrived with. */
    private void flattenScalars(Document document, String prefix, Map<String, String> out,
                                TypeProfile types) {
        for (Map.Entry<String, Object> entry : document.entrySet()) {
            String path = prefix.isEmpty() ? entry.getKey() : prefix + "." + entry.getKey();
            Object value = entry.getValue();
            if (value instanceof Document nested) {
                flattenScalars(nested, path, out, types);
            } else if (!(value instanceof List<?>)) {
                out.put(path, value == null ? config.nullString : String.valueOf(value));
                if (types != null && value != null) {
                    types.record(path, value.getClass().getSimpleName());
                }
            }
        }
    }

    private List<Map<String, String>> elementsOf(Document document, String arrayPath) {
        return elementsOf(document, arrayPath, null);
    }

    /** As above, additionally recording the BSON type each element field arrived with. */
    private List<Map<String, String>> elementsOf(Document document, String arrayPath,
                                                 TypeProfile types) {
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
                flattenScalars(elementDocument, arrayPath, flat, types);
            } else if (element != null) {
                flat.put(arrayPath, String.valueOf(element));
                if (types != null) {
                    types.record(arrayPath, element.getClass().getSimpleName());
                }
            }
            elements.add(flat);
        }
        return elements;
    }

    // ----------------------------------------------------------------------------------- helpers

    private String text(Value value) {
        return (value == null || value.isNull()) ? config.nullString : String.valueOf(value.asObject());
    }

    /** The driver's declared type for a property value, or null where there is none to record. */
    private static String typeName(Value value) {
        return (value == null || value.isNull()) ? null : value.type().name();
    }

    private String fileName(String relation) {
        return relation.replaceAll("[^A-Za-z0-9._@-]", "_") + (definedness ? ".mscope" : ".csv");
    }

    /** Minimal RFC 4180 writer; exports are read back by the standard CSV adapter. */
    private final class CsvWriter implements AutoCloseable {

        private final BufferedWriter out;
        private final MaskedScope.Writer masked;

        CsvWriter(Path file, List<String> columns) throws IOException {
            if (definedness) {
                masked = new MaskedScope.Writer(file, columns);
                out = null;
                return;
            }
            masked = null;
            this.out = Files.newBufferedWriter(file, StandardCharsets.UTF_8);
            row(columns);
        }

        void row(List<String> values) throws IOException {
            if (masked != null) { masked.row(values); return; }
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
            if (masked != null) masked.close(); else out.close();
        }
    }
}
