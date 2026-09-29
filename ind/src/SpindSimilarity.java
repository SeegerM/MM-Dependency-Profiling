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
 * counts as covered when a referenced value is similar to it (by default, within edit distance
 * tau) rather than equal to it.
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
        // The third argument is the comparison relation: a number means edit distance at that
        // radius; a name selects one of the other measures, for which tau is not used.
        String relation = args[2];
        int tau = relation.matches("-?\\d+") ? Integer.parseInt(relation) : 0;

        Config config = new Config(threshold);
        config.similarityMode = switch (relation) {
            case "prefix", "registry-prefix" -> Config.SimilarityMode.REGISTRY_PREFIX;
            case "normalised", "separator-normalised" ->
                    Config.SimilarityMode.SEPARATOR_NORMALISED;
            case "jaccard" -> Config.SimilarityMode.JACCARD;
            case "hybrid" -> Config.SimilarityMode.HYBRID;
            default -> Config.SimilarityMode.EDIT_DISTANCE;
        };
        config.editDistanceThreshold = tau;
        // A column is excluded from the similarity path when any of its values is longer than
        // this, so long-text columns may need a higher limit than the default.
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
