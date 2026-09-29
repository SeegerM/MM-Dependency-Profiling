import core.Spind;
import runner.Config;
import structures.TableSource;

import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Runs SPIND across the UniBench-IND three-store setup.
 *
 * Results are read back from SPIND's JSON output by experiments/run/verify_unibench.py.
 *
 * usage: SpindMultiModel <threshold> <tempFolder> <resultFolder>
 */
public class SpindMultiModel {

    public static void main(String[] args) throws Exception {
        double threshold = Double.parseDouble(args[0]);

        Config config = new Config(threshold);
        config.maxNary = 1;
        config.databaseName = "unibench";
        config.tempFolder = args[1];
        config.resultFolder = args[2];
        config.nullString = "";
        Files.createDirectories(Path.of(config.tempFolder));
        Files.createDirectories(Path.of(config.resultFolder));

        // Every entity the oracle refers to. The vendor sub-document nested in an order item needs
        // no separate source; the document adapter exposes it as a path.
        config.addTable(new TableSource.Postgres("pg", "public", "customer"));
        config.addTable(new TableSource.Postgres("pg", "public", "invoice"));
        config.addTable(new TableSource.Postgres("pg", "public", "vendor"));
        config.addTable(new TableSource.Mongo("mongo", "unibench", "Order"));
        config.addTable(new TableSource.Neo4jLabel("graph", "neo4j", "Person"));
        config.addTable(new TableSource.Neo4jLabel("graph", "neo4j", "Tag"));

        long start = System.currentTimeMillis();
        new Spind(config).execute();
        System.out.println("discovery took " + (System.currentTimeMillis() - start) + " ms");
        System.out.println("results in " + config.resultFolder);
    }
}
