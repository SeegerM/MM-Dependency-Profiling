package multimodel.metalevel;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;

/**
 * Per-column histogram of the native types a store declared for the values it produced.
 *
 * The meta level serializes every value to text, which discards the type the store declared; this
 * profile keeps it. Types are counted per value rather than per column, because a document or graph
 * store may type one field differently across documents or nodes.
 *
 * Written to a sidecar ({@code types.json}), never into {@code catalog.json}.
 */
public final class TypeProfile {

    private final Map<String, Map<String, Long>> byColumn = new LinkedHashMap<>();

    /** Counts one observation of {@code type} for {@code column}; null types are ignored. */
    public void record(String column, String type) {
        record(column, type, 1L);
    }

    /**
     * Counts {@code count} observations at once, e.g. a relational column whose type is declared
     * once in the catalog.
     */
    public void record(String column, String type, long count) {
        if (type == null || type.isEmpty() || count <= 0) {
            return;
        }
        byColumn.computeIfAbsent(column, key -> new TreeMap<>()).merge(type, count, Long::sum);
    }

    public boolean isEmpty() {
        return byColumn.isEmpty();
    }

    /** {@code {"column": {"type": count, ...}, ...}} as JSON. */
    public String toJson() {
        StringBuilder out = new StringBuilder("{");
        boolean firstColumn = true;
        for (Map.Entry<String, Map<String, Long>> column : byColumn.entrySet()) {
            if (!firstColumn) {
                out.append(", ");
            }
            firstColumn = false;
            out.append('"').append(escape(column.getKey())).append("\": {");
            boolean firstType = true;
            for (Map.Entry<String, Long> type : column.getValue().entrySet()) {
                if (!firstType) {
                    out.append(", ");
                }
                firstType = false;
                out.append('"').append(escape(type.getKey())).append("\": ").append(type.getValue());
            }
            out.append('}');
        }
        return out.append('}').toString();
    }

    private static String escape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
