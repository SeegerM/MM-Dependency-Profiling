import de.metanome.algorithm_integration.ColumnIdentifier;
import de.metanome.algorithm_integration.input.RelationalInput;
import de.metanome.algorithm_integration.results.Result;
import de.metanome.algorithms.hyfd.HyFD;
import de.metanome.backend.result_receiver.ResultCache;
import multimodel.adapters.MetanomeBridge;
import runner.Config;
import runner.ConnectionRegistry;
import structures.TableSource;

import java.util.ArrayList;
import java.util.List;

/**
 * Runs HyFD-Partial directly against a relational, document or graph store.
 *
 * usage: HyFdMultiModel <postgres|mongo|neo4j|file> <a> <b> [threshold] [dumpFile]
 *   postgres public customer      mongo shop orders      neo4j neo4j Part
 *
 * With a dump file the complete result set is written one FD per line, which is what any
 * downstream classification needs; the console listing is capped and only there to eyeball a run.
 */
public class HyFdMultiModel {

    public static void main(String[] args) throws Exception {
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
        HyFD hyFD = new HyFD();
        hyFD.setRelationalInputConfigurationValue(HyFD.Identifier.INPUT_GENERATOR.name(), bridge);
        hyFD.setStringConfigurationValue(HyFD.Identifier.THRESHOLD.name(), String.valueOf(threshold));
        hyFD.setIntegerConfigurationValue(HyFD.Identifier.MAX_DETERMINANT_SIZE.name(), -1);
        hyFD.setResultReceiver(cache);

        long start = System.currentTimeMillis();
        hyFD.execute();
        long elapsed = System.currentTimeMillis() - start;

        List<Result> results = cache.fetchNewResults();
        System.out.println("store      : " + model + "  (" + source.displayName() + ")");
        System.out.println("extractors : " + columns.size());
        System.out.println("threshold  : " + threshold);
        System.out.println("FDs        : " + results.size() + "   (" + elapsed + " ms)");
        results.stream().limit(10).forEach(r -> System.out.println("   " + r));

        if (dumpFile != null) {
            java.nio.file.Files.write(java.nio.file.Path.of(dumpFile),
                    results.stream().map(Object::toString).toList());
            System.out.println("dumped     : " + dumpFile);
        }
    }
}
