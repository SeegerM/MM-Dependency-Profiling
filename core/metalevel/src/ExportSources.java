import multimodel.metalevel.MetaLevelExporter;
import runner.Config;
import runner.ConnectionRegistry;
import structures.TableSource;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Exports the sources a specification file names.
 *
 * The format is one source per line, whitespace-separated, `#` to end of line being a comment:
 *
 *   postgres     &lt;connection&gt; &lt;schema&gt;   &lt;table&gt;
 *   mongo        &lt;connection&gt; &lt;database&gt; &lt;collection&gt;
 *   neo4j-label  &lt;connection&gt; &lt;database&gt; &lt;label&gt;
 *   neo4j-rels   &lt;connection&gt; &lt;database&gt; &lt;prefix|*&gt;
 *   union        &lt;relation&gt;   &lt;member,...&gt; &lt;column,...&gt;
 *
 * `neo4j-rels` takes a prefix rather than a type name because one graph store holds several
 * corpora at once and relationship types are not namespaced by node label; `*` exports every type,
 * which is only correct when the store holds one corpus.
 *
 * `union` names no store. It builds a scope whose matches are those of already exported scopes, as
 * needed for a reference into a taxonomy (e.g. MMSBench-Net's six device labels). Members are
 * declared rather than searched, since the number of possible unions is exponential.
 *
 * usage: ExportSources &lt;specFile&gt; &lt;outDir&gt; [--with-parent-reference] [--flat]
 *
 * {@code --flat} drops the relationship scopes, which is the component ablation: it exports what an
 * adapter that presents a store as one relation per entity type is able to see.
 */
public class ExportSources {

    public static void main(String[] args) throws Exception {
        Path spec = Path.of(args[0]);
        Path outDir = Path.of(args[1]);
        List<String> options = List.of(args).subList(Math.min(2, args.length), args.length);
        boolean withParentReference = options.contains("--with-parent-reference");
        boolean flat = options.contains("--flat");

        Config config = new Config(1.0);
        config.nullString = "";
        ConnectionRegistry registry = ConnectionRegistry.loadDefault();
        MetaLevelExporter exporter = new MetaLevelExporter(config, registry, withParentReference);

        List<TableSource> sources = new ArrayList<>();
        List<String[]> relationshipGroups = new ArrayList<>();
        List<String[]> unionGroups = new ArrayList<>();
        int lineNumber = 0;
        for (String raw : Files.readAllLines(spec)) {
            lineNumber++;
            String line = raw.split("#", 2)[0].trim();
            if (line.isEmpty()) {
                continue;
            }
            String[] parts = line.split("\\s+");
            if (parts.length != 4) {
                throw new IllegalArgumentException(
                        spec + ":" + lineNumber + ": expected four tokens, got " + parts.length);
            }
            switch (parts[0]) {
                case "postgres" -> sources.add(new TableSource.Postgres(parts[1], parts[2], parts[3]));
                case "mongo" -> sources.add(new TableSource.Mongo(parts[1], parts[2], parts[3]));
                case "neo4j-label" -> sources.add(new TableSource.Neo4jLabel(parts[1], parts[2], parts[3]));
                case "neo4j-rels" -> {
                    if (!flat) {
                        relationshipGroups.add(new String[]{parts[1], parts[2], parts[3]});
                    }
                }
                // Members are resolved after every base scope has been exported.
                case "union" -> unionGroups.add(new String[]{parts[1], parts[2], parts[3]});
                default -> throw new IllegalArgumentException(
                        spec + ":" + lineNumber + ": unknown source kind " + parts[0]);
            }
        }

        List<MetaLevelExporter.Scope> all = new ArrayList<>();
        for (TableSource source : sources) {
            all.addAll(exporter.export(source, outDir));
        }
        // Relationship types are scopes of the graph store as a whole rather than of any one label,
        // so they are exported once per store instead of per source.
        for (String[] group : relationshipGroups) {
            String prefix = group[2];
            all.addAll(exporter.exportRelationships(group[0], group[1], outDir,
                    "*".equals(prefix) ? type -> true : type -> type.startsWith(prefix)));
        }
        // Unions last, since every member must already have been exported. A member that matches no
        // exported scope is an error rather than an empty contribution.
        for (String[] group : unionGroups) {
            List<String> memberNames = List.of(group[1].split(","));
            List<MetaLevelExporter.Scope> members = new ArrayList<>();
            for (String name : memberNames) {
                all.stream().filter(s -> s.relation().equals(name)).findFirst().ifPresent(members::add);
            }
            if (members.size() != memberNames.size()) {
                throw new IllegalArgumentException(spec + ": union " + group[0]
                        + " names " + memberNames.size() + " members but only "
                        + members.size() + " were exported");
            }
            all.add(exporter.exportUnion(group[0], List.of(group[2].split(",")), members, outDir));
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
            System.out.printf("  %-40s scope=%-16s %,11d rows x %d cols%n",
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
