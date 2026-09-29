import com.google.gson.JsonObject;
import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoDatabase;

import org.bson.Document;
import org.neo4j.driver.AuthTokens;
import org.neo4j.driver.Driver;
import org.neo4j.driver.GraphDatabase;
import org.neo4j.driver.Session;
import org.polypheny.simpleclient.scenario.coms.ComsConfig;
import org.polypheny.simpleclient.scenario.coms.simulation.NetworkGenerator;
import org.polypheny.simpleclient.scenario.coms.simulation.User;
import org.polypheny.simpleclient.scenario.coms.simulation.User.Login;
import org.polypheny.simpleclient.scenario.coms.simulation.entites.Graph.Edge;
import org.polypheny.simpleclient.scenario.coms.simulation.entites.Graph.Node;

import runner.ConnectionRegistry;

import java.io.InputStream;
import java.lang.reflect.Field;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;

/**
 * Loads the MMSBench-Net scenario into this project's PostgreSQL, MongoDB and Neo4j.
 *
 * MMSBench-Net is a network-monitoring benchmark from the Polypheny group, designed natively across
 * three models. Only its simulation is used: the generator returns plain collections, which are
 * written straight into the stores without a running Polypheny instance.
 *
 * Only the {@code ComsConfig(String, Properties, int)} constructor can read the shipped
 * {@code coms.properties} (the Map variant parses its decimal percentages as integers). Its first
 * argument is the benchmark system, not a data store.
 *
 * The three models carry the correspondences the profile should find:
 *   login.deviceid  -> device node id      relational to graph
 *   login.userid    -> user.id             relational to relational
 *   logs.deviceid   -> device node id      document to graph
 *   logs users      -> user.id             document array to relational
 *   logs user.id    -> user.id             nested document to relational
 *
 * The network is seeded from coms.properties and reproducible, except login.accesstime: upstream's
 * createRandomTimestamp uses ThreadLocalRandom, so FD and UCC counts over coms.login can vary
 * slightly between loads.
 *
 * usage: LoadComs [networkScale]
 */
public class LoadComs {

    private static final String SCHEMA = "coms";
    private static final String MONGO_DB = "coms";

    public static void main(String[] args) throws Exception {
        Properties shipped = new Properties();
        try (InputStream in = ComsConfig.class.getResourceAsStream("coms.properties")) {
            if (in == null) {
                throw new IllegalStateException("coms.properties not on the classpath");
            }
            shipped.load(in);
        }
        for (String key : shipped.stringPropertyNames()) {
            shipped.setProperty(key, shipped.getProperty(key).replace("\"", "").trim());
        }
        if (args.length > 0) {
            shipped.setProperty("networkScale", args[0]);
        }
        shipped.putIfAbsent("memoryCatalog", "false");
        shipped.putIfAbsent("router", "Simple");
        shipped.putIfAbsent("pdbBranch", "master");

        ComsConfig config = new ComsConfig("polypheny", shipped, 1);
        NetworkGenerator.Network network = new NetworkGenerator(config).network;

        List<Node> nodes = collect(network, "nodes");
        List<Edge> edges = collect(network, "edges");
        List<User> users = network.getUsers();
        System.out.printf("generated scale=%s: %d nodes, %d edges, %d users%n",
                shipped.getProperty("networkScale"), nodes.size(), edges.size(), users.size());

        ConnectionRegistry registry = ConnectionRegistry.loadDefault();
        loadRelational(registry, users, nodes);
        loadDocuments(registry, nodes);
        loadGraph(registry, nodes, edges);
        System.out.println("loaded into postgres/" + SCHEMA + ", mongo/" + MONGO_DB + ", neo4j");
    }

    /**
     * The network exposes its entities through grouping lists of lists; flatten one of them.
     * Reflection rather than the accessors because the grouping fields are package-visible.
     */
    @SuppressWarnings("unchecked")
    private static <T> List<T> collect(NetworkGenerator.Network network, String field)
            throws Exception {
        Field declared = network.getClass().getDeclaredField(field);
        declared.setAccessible(true);
        List<T> all = new ArrayList<>();
        for (Collection<?> group : (List<Collection<?>>) declared.get(network)) {
            for (Object element : group) {
                all.add((T) element);
            }
        }
        return all;
    }

    // ------------------------------------------------------------------------------- relational

    private static void loadRelational(ConnectionRegistry registry, List<User> users,
                                       List<Node> nodes) throws Exception {
        ConnectionRegistry.PostgresConnection conn = registry.getPostgres("pg");
        try (Connection connection = DriverManager.getConnection(
                conn.url(), conn.user(), conn.password())) {
            try (Statement statement = connection.createStatement()) {
                statement.execute("DROP SCHEMA IF EXISTS " + SCHEMA + " CASCADE");
                statement.execute("CREATE SCHEMA " + SCHEMA);
                // Types follow the benchmark's own PropertyType declarations for these tables.
                statement.execute("CREATE TABLE " + SCHEMA + ".\"user\" ("
                        + "id BIGINT PRIMARY KEY, firstname VARCHAR(64), lastname VARCHAR(64),"
                        + " birthday BIGINT, salary BIGINT)");
                statement.execute("CREATE TABLE " + SCHEMA + ".login ("
                        + "userid BIGINT, deviceid BIGINT, accesstime TIMESTAMP,"
                        + " duration BIGINT, successful BOOLEAN)");
            }

            connection.setAutoCommit(false);
            try (PreparedStatement insert = connection.prepareStatement(
                    "INSERT INTO " + SCHEMA + ".\"user\" VALUES (?,?,?,?,?)")) {
                for (User user : users) {
                    Map<String, String> properties = userProperties(user);
                    insert.setLong(1, user.id);
                    insert.setString(2, strip(properties.get("firstname")));
                    insert.setString(3, strip(properties.get("lastname")));
                    insert.setLong(4, asLong(properties.get("birthday")));
                    insert.setLong(5, asLong(properties.get("salary")));
                    insert.addBatch();
                }
                insert.executeBatch();
            }

            int logins = 0;
            try (PreparedStatement insert = connection.prepareStatement(
                    "INSERT INTO " + SCHEMA + ".login VALUES (?,?,?,?,?)")) {
                for (Node node : nodes) {
                    for (Login login : node.getLogins()) {
                        Map<String, String> values = loginProperties(login);
                        insert.setLong(1, asLong(values.get("userid")));
                        insert.setLong(2, asLong(values.get("deviceid")));
                        insert.setTimestamp(3, java.sql.Timestamp.valueOf(
                                loginTimestamp(login)));
                        insert.setLong(4, asLong(values.get("duration")));
                        insert.setBoolean(5, Boolean.parseBoolean(
                                strip(values.get("successful"))));
                        insert.addBatch();
                        logins++;
                    }
                }
                insert.executeBatch();
            }
            connection.commit();
            System.out.printf("  postgres: %d users, %d logins%n", users.size(), logins);
        }
    }

    // -------------------------------------------------------------------------------- documents

    private static void loadDocuments(ConnectionRegistry registry, List<Node> nodes) {
        ConnectionRegistry.MongoConnection conn = registry.getMongo("mongo");
        try (MongoClient client = MongoClients.create(conn.uri())) {
            MongoDatabase database = client.getDatabase(MONGO_DB);
            database.getCollection("logs").drop();
            List<Document> documents = new ArrayList<>();
            for (Node node : nodes) {
                int position = 0;
                for (JsonObject log : node.nestedQueries) {
                    Document document = Document.parse(log.toString());
                    // Log entries have no identifier. A deterministic _id from node and position
                    // keeps the export stable across loads, unlike a fresh ObjectId.
                    document.put("_id", node.getId() + ":" + position++);
                    documents.add(document);
                }
            }
            if (!documents.isEmpty()) {
                database.getCollection("logs").insertMany(documents);
            }
            System.out.printf("  mongo: %d log documents%n", documents.size());
        }
    }

    // ------------------------------------------------------------------------------------ graph

    private static void loadGraph(ConnectionRegistry registry, List<Node> nodes, List<Edge> edges) {
        ConnectionRegistry.Neo4jConnection conn = registry.getNeo4j("graph");
        try (Driver driver = GraphDatabase.driver(
                     conn.uri(), AuthTokens.basic(conn.user(), conn.password()));
             Session session = driver.session()) {
            session.run("MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH 'coms_') "
                    + "DETACH DELETE n");
            for (Node node : nodes) {
                Map<String, Object> properties = new LinkedHashMap<>(node.dynProperties);
                properties.put("id", node.getId());
                session.run("CREATE (n:" + label(node) + ") SET n = $props",
                        org.neo4j.driver.Values.parameters("props", properties));
            }
            for (Edge edge : edges) {
                Map<String, Object> properties = new LinkedHashMap<>(edge.dynProperties);
                properties.put("id", edge.getId());
                session.run("MATCH (a),(b) WHERE a.id = $from AND b.id = $to "
                                + "CREATE (a)-[r:" + label(edge) + "]->(b) SET r = $props",
                        org.neo4j.driver.Values.parameters(
                                "from", edgeEndpoint(edge, "from"),
                                "to", edgeEndpoint(edge, "to"),
                                "props", properties));
            }
            System.out.printf("  neo4j: %d nodes, %d edges%n", nodes.size(), edges.size());
        }
    }

    /** Labels are prefixed so the scenario can share a Neo4j instance with other corpora. */
    private static String label(Object element) {
        return "coms_" + element.getClass().getSimpleName().toLowerCase();
    }

    // ---------------------------------------------------------------------------------- helpers

    @SuppressWarnings("unchecked")
    private static Map<String, String> userProperties(User user) throws Exception {
        Field field = User.class.getDeclaredField("properties");
        field.setAccessible(true);
        return (Map<String, String>) field.get(user);
    }

    private static Map<String, String> loginProperties(Login login) throws Exception {
        Map<String, String> values = new LinkedHashMap<>();
        // The benchmark calls the login duration "mins"; the column it lands in is "duration".
        Map<String, String> columnOf = Map.of("userId", "userid", "deviceId", "deviceid",
                "mins", "duration", "successful", "successful");
        for (Map.Entry<String, String> entry : columnOf.entrySet()) {
            Field field = Login.class.getDeclaredField(entry.getKey());
            field.setAccessible(true);
            values.put(entry.getValue(), String.valueOf(field.get(login)));
        }
        return values;
    }

    private static java.time.LocalDateTime loginTimestamp(Login login) throws Exception {
        Field field = Login.class.getDeclaredField("timestamp");
        field.setAccessible(true);
        return (java.time.LocalDateTime) field.get(login);
    }

    private static long edgeEndpoint(Edge edge, String name) {
        try {
            Field field = Edge.class.getDeclaredField(name);
            field.setAccessible(true);
            return (long) field.get(edge);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException("edge endpoint " + name, e);
        }
    }

    /** The generator quotes string values for SQL; the quotes are not part of the value. */
    private static String strip(String value) {
        if (value == null) {
            return null;
        }
        String trimmed = value.trim();
        if (trimmed.length() > 1 && trimmed.startsWith("'") && trimmed.endsWith("'")) {
            return trimmed.substring(1, trimmed.length() - 1);
        }
        return trimmed;
    }

    private static long asLong(String value) {
        String text = strip(value);
        if (text == null || text.isEmpty()) {
            return 0L;
        }
        try {
            return (long) Double.parseDouble(text);
        } catch (NumberFormatException e) {
            return 0L;
        }
    }
}
