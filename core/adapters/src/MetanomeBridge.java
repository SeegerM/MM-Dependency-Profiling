package multimodel.adapters;

import de.metanome.algorithm_integration.AlgorithmConfigurationException;
import de.metanome.algorithm_integration.input.InputGenerationException;
import de.metanome.algorithm_integration.input.InputIterationException;
import de.metanome.algorithm_integration.input.RelationalInput;
import de.metanome.algorithm_integration.input.RelationalInputGenerator;
import io.RelationalInputFactory;
import runner.Config;
import runner.ConnectionRegistry;
import structures.TableSource;

import java.util.Arrays;
import java.util.List;

/**
 * Presents any SPIND {@link TableSource} to Metanome algorithms as a {@code RelationalInput}.
 *
 * <p>SPIND's adapters already produce rows of strings, which is what Metanome's input interface
 * expects, so HyFD, HyUCC and other Metanome algorithms can read the same sources unchanged.</p>
 */
public final class MetanomeBridge implements RelationalInputGenerator {

    private final TableSource source;
    private final Config config;
    private final ConnectionRegistry registry;

    public MetanomeBridge(TableSource source, Config config, ConnectionRegistry registry) {
        this.source = source;
        this.config = config;
        this.registry = registry;
    }

    /**
     * Metanome algorithms read their input more than once — HyFD builds position list indexes and
     * then samples — so each call opens a fresh stream rather than rewinding a shared one.
     */
    @Override
    public RelationalInput generateNewCopy() throws InputGenerationException {
        if (source instanceof TableSource.File file && file.path().toString().endsWith(".mscope")) {
            throw new InputGenerationException("Masked scopes require CompleteMatchDiscovery; legacy null semantics are incompatible");
        }
        // Document and graph stores must go through the meta-level export (one observation set per
        // scope); streaming them directly would serialize list-valued fields.
        if (source instanceof TableSource.Mongo || source instanceof TableSource.Neo4jLabel) {
            throw new InputGenerationException(
                    "Streaming " + source.displayName() + " directly is retired: a collection-valued "
                    + "field would be observed as a serialized literal. Export it first with "
                    + "core/metalevel/MetaLevelExporter and read the exported observation sets.");
        }
        try {
            return new Adapter(RelationalInputFactory.open(source, config, registry),
                    source.displayName(), config.nullString);
        } catch (Exception e) {
            throw new InputGenerationException("cannot open " + source.displayName(), e);
        }
    }

    @Override
    public void close() {
    }

    private static final class Adapter implements RelationalInput {

        private final io.RelationalInput inner;
        private final String relationName;
        private final String nullString;
        private final List<String> columnNames;

        Adapter(io.RelationalInput inner, String relationName, String nullString) {
            this.inner = inner;
            this.relationName = relationName;
            this.nullString = nullString;
            this.columnNames = Arrays.asList(inner.getHeader());
        }

        @Override
        public boolean hasNext() {
            return inner.hasNext();
        }

        @Override
        public List<String> next() throws InputIterationException {
            String[] row = inner.next();
            if (row == null) {
                throw new InputIterationException("input ended before hasNext() said so");
            }
            String[] values = new String[columnNames.size()];
            for (int i = 0; i < values.length; i++) {
                String value = i < row.length ? row[i] : null;
                // SPIND emits the configured null string; Metanome expects a Java null.
                values[i] = (value == null || value.equals(nullString)) ? null : value;
            }
            return Arrays.asList(values);
        }

        @Override
        public int numberOfColumns() {
            return columnNames.size();
        }

        @Override
        public String relationName() {
            return relationName;
        }

        @Override
        public List<String> columnNames() {
            return columnNames;
        }

        @Override
        public void close() throws Exception {
            inner.close();
        }
    }
}
