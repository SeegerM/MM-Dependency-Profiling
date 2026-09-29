import multimodel.metalevel.MetaLevelExporter;
import runner.Config;
import runner.ConnectionRegistry;
import structures.TableSource;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Exports the UniBench-IND stores to the meta level, one observation set per scope, and records a
 * catalog of what was produced.
 *
 * usage: ExportUniBench &lt;outDir&gt;
 */
public class ExportUniBench {

    public static void main(String[] args) throws Exception {
        Path outDir = Path.of(args[0]);
        Config config = new Config(1.0);
        config.nullString = "";
        ConnectionRegistry registry = ConnectionRegistry.loadDefault();

        List<TableSource> sources = List.of(
                new TableSource.Postgres("pg", "public", "customer"),
                new TableSource.Postgres("pg", "public", "invoice"),
                new TableSource.Postgres("pg", "public", "vendor"),
                new TableSource.Mongo("mongo", "unibench", "Order"),
                new TableSource.Neo4jLabel("graph", "neo4j", "Person"),
                new TableSource.Neo4jLabel("graph", "neo4j", "Tag"));

        MetaLevelExporter exporter = new MetaLevelExporter(config, registry);
        List<MetaLevelExporter.Scope> all = new ArrayList<>();
        for (TableSource source : sources) {
            all.addAll(exporter.export(source, outDir));
        }
        all.addAll(exporter.exportRelationships("graph", "neo4j", outDir));

        StringBuilder catalog = new StringBuilder("[\n");
        for (int i = 0; i < all.size(); i++) {
            MetaLevelExporter.Scope scope = all.get(i);
            catalog.append("  {\"relation\": \"").append(scope.relation())
                    .append("\", \"scope\": \"").append(scope.scope())
                    .append("\", \"rows\": ").append(scope.rows())
                    .append(", \"columns\": ").append(scope.columns().size())
                    .append(", \"file\": \"").append(scope.file().getFileName()).append("\"}")
                    .append(i + 1 < all.size() ? ",\n" : "\n");
            System.out.printf("  %-42s scope=%-12s %,8d rows x %d cols%n",
                    scope.relation(), scope.scope(), scope.rows(), scope.columns().size());
        }
        catalog.append("]\n");
        Files.writeString(outDir.resolve("catalog.json"), catalog.toString());

        // Native types go beside the catalog, not into it: the discovery entry points parse the
        // catalog with a regex.
        StringBuilder types = new StringBuilder("{\n");
        for (int i = 0; i < all.size(); i++) {
            MetaLevelExporter.Scope scope = all.get(i);
            types.append("  \"").append(scope.relation()).append("\": ")
                    .append(scope.types().toJson())
                    .append(i + 1 < all.size() ? ",\n" : "\n");
        }
        types.append("}\n");
        Files.writeString(outDir.resolve("types.json"), types.toString());
        System.out.println("\n" + all.size() + " observation sets -> " + outDir);
    }
}
