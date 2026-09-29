package multimodel.adapters;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** Versioned, self-contained value/definedness transport. Row ordinals identify occurrences.
 * Metadata is positional, never part of the extractor family. Empty strings remain values.
 * The .mscope extension deliberately prevents legacy CSV-only consumers from using masks as data.
 */
public final class MaskedScope {
    public static final String MAGIC = "#multimodel-scope-v2";
    private MaskedScope() {}

    public static final class Writer implements AutoCloseable {
        private final BufferedWriter out;
        private final int width;
        private long ordinal;
        public Writer(Path path, List<String> columns) throws IOException {
            if (new HashSet<>(columns).size() != columns.size())
                throw new IOException("Duplicate extractor names");
            width = columns.size();
            out = Files.newBufferedWriter(path, StandardCharsets.UTF_8);
            List<String> header = new ArrayList<>(List.of(MAGIC, "defined"));
            header.addAll(columns);
            write(header);
        }
        public void row(List<String> values) throws IOException {
            if (values.size() != width) throw new IOException("Scope row width mismatch");
            StringBuilder mask = new StringBuilder(width);
            for (String value : values) mask.append(value == null ? '0' : '1');
            List<String> fields = new ArrayList<>(List.of(Long.toString(ordinal++), mask.toString()));
            for (String value : values) fields.add(value == null ? "" : value);
            write(fields);
        }
        private void write(List<String> fields) throws IOException {
            for (int i = 0; i < fields.size(); i++) {
                if (i > 0) out.write(',');
                out.write('"');
                out.write(fields.get(i).replace("\"", "\"\""));
                out.write('"');
            }
            out.write('\n');
        }
        public void close() throws IOException { out.close(); }
    }

    public static final class Reader implements AutoCloseable {
        private final PushbackReader csv;
        public final List<String> columns;
        private long ordinal;
        public Reader(Path path) throws IOException {
            csv = new PushbackReader(Files.newBufferedReader(path, StandardCharsets.UTF_8), 1);
            try {
                String[] header = readRecord();
                if (header == null || header.length < 2 || !MAGIC.equals(header[0])
                        || !"defined".equals(header[1])) throw new IOException("Not a v2 masked scope: " + path);
                columns = List.copyOf(Arrays.asList(header).subList(2, header.length));
                if (new HashSet<>(columns).size() != columns.size()) throw new IOException("Duplicate extractor names");
            } catch (Exception e) {
                csv.close();
                throw new IOException("Invalid masked scope header: " + path, e);
            }
        }
        public String[] next() throws IOException {
            try {
                String[] fields = readRecord();
                if (fields == null) return null;
                int width = columns.size();
                if (fields.length != width + 2 || !Long.toString(ordinal).equals(fields[0])
                        || fields[1].length() != width) throw new IOException("Invalid row shape or occurrence id at " + ordinal);
                String[] values = new String[width];
                for (int c = 0; c < width; c++) {
                    char bit = fields[1].charAt(c);
                    if (bit != '0' && bit != '1') throw new IOException("Invalid definedness bit");
                    if (bit == '0' && !fields[c + 2].isEmpty()) throw new IOException("Noncanonical missing payload");
                    values[c] = bit == '1' ? fields[c + 2] : null;
                }
                ordinal++;
                return values;
            } catch (Exception e) { throw new IOException("Invalid masked scope row " + ordinal, e); }
        }
        public void close() throws IOException { csv.close(); }
        // Parse characters rather than physical lines: quoted CR, LF and CRLF are data,
        // whereas CRLF and LF outside quotes terminate a record.
        private String[] readRecord() throws IOException {
            List<String> fields = new ArrayList<>();
            StringBuilder field = new StringBuilder();
            int state = 0; // 0: field start, 1: unquoted, 2: quoted, 3: closing quote
            boolean seen = false;
            for (;;) {
                int ch = csv.read();
                if (ch == -1) {
                    if (state == 2) throw new IOException("Unclosed quoted field");
                    if (!seen) return null;
                    fields.add(field.toString());
                    return fields.toArray(String[]::new);
                }
                seen = true;
                if (state == 2) {
                    if (ch == '"') state = 3;
                    else field.append((char) ch);
                    continue;
                }
                if (state == 3 && ch == '"') { field.append('"'); state = 2; continue; }
                if (ch == ',' || ch == '\n' || ch == '\r') {
                    fields.add(field.toString()); field.setLength(0); state = 0;
                    if (ch == ',') continue;
                    if (ch == '\r') {
                        int following = csv.read();
                        if (following != '\n' && following != -1) csv.unread(following);
                    }
                    return fields.toArray(String[]::new);
                }
                if (state == 3) throw new IOException("Characters after closing quote");
                if (ch == '"') {
                    if (state != 0) throw new IOException("Quote in unquoted field");
                    state = 2;
                } else { field.append((char) ch); state = 1; }
            }
        }
    }
}
