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
 * maxNary defaults to 1. Higher values let SPIND climb the lattice, which is where IND discovery
 * becomes expensive and where the candidate counts the paper reasons about actually arise.
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
        // Restricting candidates to one comparison domain is a property of the run, not of the
        // export, so it is a switch rather than an argument: the same catalog is discovered over
        // both ways when the question is what typing costs and buys.
        config.typedComparison = Boolean.getBoolean("spind.typedComparison");
        config.collectColumnStats = config.collectColumnStats || config.typedComparison;
        // Domain pruning happens on the pre-prune path, so it has to be on for the switch
        // to have any effect at all.
        config.usePrePrune = config.usePrePrune || config.typedComparison;
        config.databaseName = "unibench-metalevel";
        config.tempFolder = args[2];
        config.resultFolder = args[3];
        config.nullString = "";
        config.inputFileHasHeader = true;
        config.separator = ',';
        Files.createDirectories(Path.of(config.tempFolder));
        Files.createDirectories(Path.of(config.resultFolder));

        Matcher matcher = ENTRY.matcher(Files.readString(exportDir.resolve("catalog.json")));
        int count = 0;
        while (matcher.find()) {
            config.addTable(new TableSource.File(exportDir.resolve(matcher.group(2)), matcher.group(1)));
            count++;
        }
        System.out.println("loaded " + count + " observation sets from " + exportDir);

        long start = System.currentTimeMillis();
        new Spind(config).execute();
        System.out.println("discovery took " + (System.currentTimeMillis() - start) + " ms");
    }
}
