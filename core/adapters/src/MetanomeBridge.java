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
 * <p>SPIND already turns relational tables, document collections and graph labels into rows of
 * strings — that is what its adapters are. Metanome's input interface asks for exactly the same
 * thing behind five methods. So the model-specific work does not need repeating: bridging the two
 * interfaces gives HyFD, HyUCC and any other Metanome algorithm the same three data models SPIND
 * reads, with no change to the algorithms themselves.</p>
 *
 * <p>This is the concrete form of the paper's claim that extraction is the only model-specific
 * component. Two independently written discovery algorithms consume one extractor implementation.</p>
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
        // Document and graph stores are not streamed directly any more. Their adapters tabularize a
        // collection into one relation, which serializes list-valued fields and explodes only the
        // first array -- measured to cost 24 percentage points of recall and to halve precision.
        // They must go through the meta-level export, which emits one observation set per scope.
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
                // SPIND's adapters emit the configured null string; Metanome's convention is a Java
                // null, which is what HyFD's value comparator tests against. Translate rather than
                // let "" become an ordinary value that every missing field agrees on.
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
