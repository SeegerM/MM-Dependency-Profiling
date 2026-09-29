import de.metanome.algorithm_integration.ColumnIdentifier;
import de.metanome.algorithm_integration.input.RelationalInput;
import de.metanome.algorithm_integration.results.Result;
import de.metanome.algorithms.hyucc.HyUCC;
import de.metanome.backend.result_receiver.ResultCache;
import multimodel.adapters.MetanomeBridge;
import runner.Config;
import runner.ConnectionRegistry;
import structures.TableSource;

import java.util.ArrayList;
import java.util.List;

/**
 * Runs HyUCC-Partial directly against a relational, document or graph store, through the same
 * SPIND->Metanome bridge HyFD uses.
 *
 * usage: HyUccMultiModel <postgres|mongo|neo4j|file> <a> <b> [threshold] [dumpFile]
 *
 * With a dump file the complete result set is written one UCC per line. The console
 * listing shows only the minimal-arity ones.
 */
public class HyUccMultiModel {

    public static void main(String[] args) throws Exception {
        if ("file".equals(args[0]) && args[1].endsWith(".mscope")) {
            CompleteMatchDiscovery.runWrapper(args, "uccs");
            return;
        }
        String model = args[0];
        double threshold = args.length > 3 ? Double.parseDouble(args[3]) : 1.0;
        String dumpFile = args.length > 4 ? args[4] : null;

        Config config = new Config(threshold);
        config.nullString = "";
        if ("file".equals(model)) {
            config.separator = ',';
            config.inputFileHasHeader = true;
        }
        ConnectionRegistry registry = ConnectionRegistry.loadDefault();

        TableSource source = switch (model) {
            case "postgres" -> new TableSource.Postgres("pg", args[1], args[2]);
            case "mongo" -> new TableSource.Mongo("mongo", args[1], args[2]);
            case "neo4j" -> new TableSource.Neo4jLabel("graph", args[1], args[2]);
            // An exported observation set. Document and graph stores are read this way.
            case "file" -> new TableSource.File(java.nio.file.Path.of(args[1]));
            default -> throw new IllegalArgumentException("unknown model: " + model);
        };

        MetanomeBridge bridge = new MetanomeBridge(source, config, registry);

        RelationalInput peek = bridge.generateNewCopy();
        List<ColumnIdentifier> columns = new ArrayList<>();
        for (String column : peek.columnNames()) {
            columns.add(new ColumnIdentifier(peek.relationName(), column));
        }
        peek.close();

        ResultCache cache = new ResultCache("multimodel", columns);
        HyUCC hyUCC = new HyUCC();
        hyUCC.setRelationalInputConfigurationValue(HyUCC.Identifier.INPUT_GENERATOR.name(), bridge);
        hyUCC.setStringConfigurationValue(HyUCC.Identifier.THRESHOLD.name(), String.valueOf(threshold));
        hyUCC.setIntegerConfigurationValue(HyUCC.Identifier.MAX_UCC_SIZE.name(), -1);
        hyUCC.setResultReceiver(cache);

        long start = System.currentTimeMillis();
        hyUCC.execute();
        long elapsed = System.currentTimeMillis() - start;

        List<Result> results = cache.fetchNewResults();
        System.out.println("store      : " + model + "  (" + source.displayName() + ")");
        System.out.println("extractors : " + columns.size());
        System.out.println("threshold  : " + threshold);
        System.out.println("UCCs       : " + results.size() + "   (" + elapsed + " ms)");
        java.util.Map<Integer, Integer> byArity = new java.util.TreeMap<>();
        boolean unaryKey = false;
        for (Result r : results) {
            String text = r.toString();
            int arity = text.substring(0, text.indexOf(']') + 1).split(",").length;
            byArity.merge(arity, 1, Integer::sum);
            if (arity == 1) unaryKey = true;
        }
        System.out.println("by arity   : " + byArity);
        System.out.println("any unary  : " + unaryKey);
        if (dumpFile != null) {
            java.nio.file.Files.write(java.nio.file.Path.of(dumpFile),
                    results.stream().map(Object::toString).toList());
            System.out.println("dumped     : " + dumpFile);
        }

        // Print the minimal-arity UCCs.
        int minArity = byArity.isEmpty() ? 0 : byArity.keySet().iterator().next();
        for (Result r : results) {
            String text = r.toString();
            if (text.substring(0, text.indexOf(']') + 1).split(",").length == minArity) {
                System.out.println("   " + text);
            }
        }
    }
}
