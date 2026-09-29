import core.Spind;
import runner.Config;
import structures.TableSource;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Runs SPIND's similarity path over a meta-level export.
 *
 * The only difference from {@link SpindMetaLevel} is the matching relation: a dependent value
 * counts as covered when a referenced value is within edit distance tau of it, rather than equal
 * to it. Everything else -- the observations, the partial threshold, the violation budget -- is
 * the same, which is the point: similarity is a property of the comparison, not of the pipeline.
 *
 * Reading the export rather than the stores matters more here than for equality. A vendor name
 * observed as the literal ["EA_Sports"] is not merely unequal to EA_Sports, it is far from it: the
 * brackets and quotes are four edits before any character of the name is considered. Similarity
 * over a serialized collection is not a weaker form of matching, it is meaningless.
 *
 * usage: SpindSimilarity &lt;exportDir&gt; &lt;threshold&gt; &lt;tau&gt; &lt;tempFolder&gt; &lt;resultFolder&gt;
 */
public class SpindSimilarity {

    private static final Pattern ENTRY =
            Pattern.compile("\"relation\":\\s*\"([^\"]+)\".*?\"file\":\\s*\"([^\"]+)\"",
                            Pattern.DOTALL);

    public static void main(String[] args) throws Exception {
        Path exportDir = Path.of(args[0]);
        double threshold = Double.parseDouble(args[1]);
        int tau = Integer.parseInt(args[2]);

        Config config = new Config(threshold);
        config.similarityMode = Config.SimilarityMode.EDIT_DISTANCE;
        config.editDistanceThreshold = tau;
        // A column is excluded from the similarity path when *any* of its values is longer than
        // this. On real text that is a trap: 0.4% of IMDB's titles exceed the default of 50
        // characters, and that 0.4% removes the whole column, so the run reports nothing and looks
        // like an absence of similar values rather than an absence of candidates.
        config.maxValueLength = Integer.getInteger("spind.maxValueLength", config.maxValueLength);
        config.maxNary = 1;
        config.databaseName = "metalevel-similarity";
        config.tempFolder = args[3];
        config.resultFolder = args[4];
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
        System.out.println("similarity: edit distance, tau = " + tau
                + ", partial threshold = " + threshold);

        long start = System.currentTimeMillis();
        new Spind(config).execute();
        System.out.println("discovery took " + (System.currentTimeMillis() - start) + " ms");
    }
}
