import core.Spind;
import runner.Config;
import structures.TableSource;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Runs SPIND over a meta-level export rather than over the stores directly.
 *
 * Each exported file is one observation set for one scope, so a list-valued field contributes one
 * observation per element instead of a bracketed literal. The relation name is taken from the
 * catalog rather than the file name, since sanitising a name for the filesystem is lossy.
 *
 * usage: SpindMetaLevel &lt;exportDir&gt; &lt;threshold&gt; &lt;tempFolder&gt; &lt;resultFolder&gt; [maxNary]
 *
 * maxNary defaults to 1; higher values let SPIND discover n-ary INDs.
 */
public class SpindMetaLevel {

    private static final Pattern ENTRY =
            Pattern.compile("\"relation\":\\s*\"([^\"]+)\".*?\"file\":\\s*\"([^\"]+)\"",
                            Pattern.DOTALL);

    public static void main(String[] args) throws Exception {
        Path exportDir = Path.of(args[0]);
        double threshold = Double.parseDouble(args[1]);

        Config config = new Config(threshold);
        config.maxNary = args.length > 4 ? Integer.parseInt(args[4]) : 1;
        // Restricts candidates to one comparison domain.
        config.typedComparison = Boolean.getBoolean("spind.typedComparison");
        config.collectColumnStats = config.collectColumnStats || config.typedComparison;
        // Domain pruning happens on the pre-prune path, so it must be enabled too.
        config.usePrePrune = config.usePrePrune || config.typedComparison;
        // Physical validation strategies; all must produce the same result set.
        config.usePrePrune = config.usePrePrune || Boolean.getBoolean("spind.prePrune");
        config.useFastValidation = Boolean.getBoolean("spind.fastValidation");
        config.useInMemoryValidation = Boolean.getBoolean("spind.inMemory");
        config.usePartitionedValidation = Boolean.getBoolean("spind.partitioned");
        // The partitioned validator reads either SPIND's intermediate chunks or the exported
        // observation sets again; both must give identical results.
        config.usePartitionFromSource = Boolean.getBoolean("spind.partitionFromSource");
        // Chunking must not affect the result. Long.MAX_VALUE disables it.
        String chunkThreshold = System.getProperty("spind.chunkThresholdBytes");
        if (chunkThreshold != null) {
            config.chunkThresholdBytes = Long.parseLong(chunkThreshold);
        }
        config.collectColumnStats = config.collectColumnStats || config.usePrePrune;
        config.databaseName = "unibench-metalevel";
        config.tempFolder = args[2];
        config.resultFolder = args[3];
        config.nullString = "";
        config.inputFileHasHeader = true;
        config.separator = ',';
        // MetaLevelExporter writes RFC 4180, where a backslash is data. OpenCSV's default backslash
        // escape would read a quoted value ending in '\\' as escaping its closing quote.
        config.fileEscape = '\0';
        Files.createDirectories(Path.of(config.tempFolder));
        Files.createDirectories(Path.of(config.resultFolder));

        Matcher matcher = ENTRY.matcher(Files.readString(exportDir.resolve("catalog.json")));
        int count = 0;
        while (matcher.find()) {
            if (matcher.group(2).endsWith(".mscope"))
                throw new IllegalArgumentException("Masked scopes require a definedness-aware consumer; SPIND CSV ingestion is not yet migrated");
            config.addTable(new TableSource.File(exportDir.resolve(matcher.group(2)), matcher.group(1)));
            count++;
        }
        System.out.println("loaded " + count + " observation sets from " + exportDir);

        long start = System.currentTimeMillis();
        new Spind(config).execute();
        System.out.println("discovery took " + (System.currentTimeMillis() - start) + " ms");
    }
}
