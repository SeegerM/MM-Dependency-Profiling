import de.metanome.algorithm_integration.configuration.ConfigurationSettingFileInput;
import de.metanome.algorithm_integration.input.RelationalInput;
import de.metanome.algorithm_integration.input.RelationalInputGenerator;
import de.metanome.algorithm_integration.results.Result;
import de.metanome.algorithms.hyfd.HyFD;
import de.metanome.backend.input.file.DefaultFileInputGenerator;
import de.metanome.backend.result_receiver.ResultCache;
import de.metanome.algorithm_integration.ColumnIdentifier;

import java.io.File;
import java.util.ArrayList;
import java.util.List;

/**
 * Integration readiness probe: drive HyFD-Partial as a library over one meta-level CSV.
 *
 * usage: HyFdSmokeTest <csv> <threshold>
 */
public class HyFdSmokeTest {
    public static void main(String[] args) throws Exception {
        File file = new File(args[0]);
        double threshold = args.length > 1 ? Double.parseDouble(args[1]) : 1.0;
        final char ESCAPE = (char) 92;  // backslash, without the literal

        RelationalInputGenerator input = new DefaultFileInputGenerator(
                new ConfigurationSettingFileInput(file.getAbsolutePath(), true,
                        ',', '"', ESCAPE, false, true, 0, true, true, ""));

        RelationalInput peek = input.generateNewCopy();
        List<ColumnIdentifier> columns = new ArrayList<>();
        for (String c : peek.columnNames()) columns.add(new ColumnIdentifier(peek.relationName(), c));

        ResultCache cache = new ResultCache("probe", columns);
        HyFD hyFD = new HyFD();
        hyFD.setRelationalInputConfigurationValue(HyFD.Identifier.INPUT_GENERATOR.name(), input);
        hyFD.setStringConfigurationValue(HyFD.Identifier.THRESHOLD.name(), String.valueOf(threshold));
        hyFD.setIntegerConfigurationValue(HyFD.Identifier.MAX_DETERMINANT_SIZE.name(), -1);
        hyFD.setResultReceiver(cache);

        long start = System.currentTimeMillis();
        hyFD.execute();
        long elapsed = System.currentTimeMillis() - start;

        List<Result> results = cache.fetchNewResults();
        System.out.println("file      : " + file.getName());
        System.out.println("threshold : " + threshold);
        System.out.println("FDs       : " + results.size() + "  (" + elapsed + " ms)");
        results.stream().limit(8).forEach(r -> System.out.println("   " + r));
    }
}
