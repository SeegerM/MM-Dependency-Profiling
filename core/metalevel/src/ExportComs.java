import multimodel.metalevel.MetaLevelExporter;
import runner.Config;
import runner.ConnectionRegistry;
import structures.TableSource;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Exports the MMSBench-Net instance loaded by {@link LoadComs} to the meta level.
 *
 * Device nodes carry generated property names ({@code key0}, {@code key1}, ...), so cross-model
 * correspondences on the graph side must be found from values alone. The six device labels form a
 * taxonomy: they share four properties and only {@code coms_pc} carries the other six.
 *
 * usage: ExportComs &lt;outDir&gt;
 */
public class ExportComs {

    /** Everything this scenario owns is prefixed by the loader, since the stores are shared. */
    private static final String PREFIX = "coms_";

    public static void main(String[] args) throws Exception {
        Path outDir = Path.of(args[0]);
        Config config = new Config(1.0);
        config.nullString = "";
        ConnectionRegistry registry = ConnectionRegistry.loadDefault();

        List<TableSource> sources = new ArrayList<>(List.of(
                new TableSource.Postgres("pg", "coms", "user"),
                new TableSource.Postgres("pg", "coms", "login"),
                new TableSource.Mongo("mongo", "coms", "logs")));
        // The device taxonomy, listed rather than discovered, so the export is reproducible even
        // if a different network scale happens to generate no node of some type.
        for (String device : List.of("ap", "iot", "mobile", "pc", "server", "switch")) {
            sources.add(new TableSource.Neo4jLabel("graph", "neo4j", PREFIX + device));
        }

        MetaLevelExporter exporter = new MetaLevelExporter(config, registry, false);
        List<MetaLevelExporter.Scope> all = new ArrayList<>();
        for (TableSource source : sources) {
            all.addAll(exporter.export(source, outDir));
        }
        all.addAll(exporter.exportRelationships(
                "graph", "neo4j", outDir, type -> type.startsWith(PREFIX)));

        // The device union: a login names a device of any kind, so `login.deviceid` is included in
        // the union of the six identifier sets but in none of them individually. The union is
        // declared from the same taxonomy the labels are exported from rather than searched for.
        List<MetaLevelExporter.Scope> deviceLabels = new ArrayList<>();
        for (MetaLevelExporter.Scope scope : all) {
            if (scope.relation().startsWith("neo4j:" + PREFIX) && "base".equals(scope.scope())
                    && !scope.relation().contains("-[")) {
                deviceLabels.add(scope);
            }
        }
        if (!deviceLabels.isEmpty()) {
            // No `neo4j:` prefix: a union is a derived scope, not a graph label.
            all.add(exporter.exportUnion("coms.device-union",
                    List.of("id"), deviceLabels, outDir));
        }

        StringBuilder catalog = new StringBuilder("[\n");
        int attributes = 0;
        for (int i = 0; i < all.size(); i++) {
            MetaLevelExporter.Scope scope = all.get(i);
            attributes += scope.columns().size();
            catalog.append("  {\"relation\": \"").append(scope.relation())
                    .append("\", \"scope\": \"").append(scope.scope())
                    .append("\", \"rows\": ").append(scope.rows())
                    .append(", \"columns\": ").append(scope.columns().size())
                    .append(", \"file\": \"").append(scope.file().getFileName()).append("\"")
                    .append(scope.members().isEmpty() ? "" : ", \"members\": [\""
                            + String.join("\", \"", scope.members()) + "\"]")
                    .append("}")
                    .append(i + 1 < all.size() ? ",\n" : "\n");
            System.out.printf("  %-32s scope=%-12s %,9d rows x %d cols%n",
                    scope.relation(), scope.scope(), scope.rows(), scope.columns().size());
        }
        catalog.append("]\n");
        Files.writeString(outDir.resolve("catalog.json"), catalog.toString());

        StringBuilder types = new StringBuilder("{\n");
        for (int i = 0; i < all.size(); i++) {
            MetaLevelExporter.Scope scope = all.get(i);
            types.append("  \"").append(scope.relation()).append("\": ")
                    .append(scope.types().toJson())
                    .append(i + 1 < all.size() ? ",\n" : "\n");
        }
        types.append("}\n");
        Files.writeString(outDir.resolve("types.json"), types.toString());
        System.out.printf("%n%d observation sets, %d attributes -> %s%n",
                all.size(), attributes, outDir);
    }
}
