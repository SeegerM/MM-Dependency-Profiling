import multimodel.metalevel.MetaLevelExporter;
import runner.Config;
import runner.ConnectionRegistry;
import structures.TableSource;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Exports the TPC-H-derived three-store instance to the meta level, one observation set per scope.
 *
 * The flat relational counterpart, with identical values in seven relations, is written by
 * {@code experiments/datasets/build_dataset.py}.
 *
 * usage: ExportTpch &lt;outDir&gt; [--with-parent-reference]
 *
 * The flag materializes the join-induced scope: each array element carries the identifier of the
 * document it belongs to. It is off by default, because a parent-valued column in a child scope is
 * not an extractor over that scope.
 */
public class ExportTpch {

    public static void main(String[] args) throws Exception {
        Path outDir = Path.of(args[0]);
        boolean withParentReference = args.length > 1 && "--with-parent-reference".equals(args[1]);
        Config config = new Config(1.0);
        config.nullString = "";
        ConnectionRegistry registry = ConnectionRegistry.loadDefault();

        List<TableSource> sources = List.of(
                new TableSource.Postgres("pg", "public", "customer"),
                new TableSource.Postgres("pg", "public", "nation"),
                new TableSource.Postgres("pg", "public", "region"),
                new TableSource.Mongo("mongo", "shop", "orders"),
                new TableSource.Neo4jLabel("graph", "neo4j", "Part"),
                new TableSource.Neo4jLabel("graph", "neo4j", "Supplier"));

        MetaLevelExporter exporter = new MetaLevelExporter(config, registry, withParentReference);
        List<MetaLevelExporter.Scope> all = new ArrayList<>();
        for (TableSource source : sources) {
            all.addAll(exporter.export(source, outDir));
        }
        // Relationship types are scopes of the graph store as a whole rather than of any one
        // label, so they are exported once instead of per source.
        all.addAll(exporter.exportRelationships("graph", "neo4j", outDir));

        StringBuilder catalog = new StringBuilder("[\n");
        int attributes = 0;
        for (int i = 0; i < all.size(); i++) {
            MetaLevelExporter.Scope scope = all.get(i);
            attributes += scope.columns().size();
            catalog.append("  {\"relation\": \"").append(scope.relation())
                    .append("\", \"scope\": \"").append(scope.scope())
                    .append("\", \"rows\": ").append(scope.rows())
                    .append(", \"columns\": ").append(scope.columns().size())
                    .append(", \"file\": \"").append(scope.file().getFileName()).append("\"}")
                    .append(i + 1 < all.size() ? ",\n" : "\n");
            System.out.printf("  %-32s scope=%-12s %,9d rows x %d cols%n",
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
        System.out.printf("%n%d observation sets, %d attributes -> %s%n",
                all.size(), attributes, outDir);
    }
}
