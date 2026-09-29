import multimodel.adapters.MaskedScope;
import java.io.*;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** Complete enumeration within an explicit arity bound; no monotonicity assumption.
 * Usage: CompleteMatchDiscovery input.mscope threshold maxArity outputPrefix
 * Outputs ALL qualifying nonempty UCCs and nontrivial FDs with nonempty determinants,
 * including supersets, with dependency-specific complete-row degrees. maxArity is
 * determinant/key width (not total FD width). Use -1 for the full finite lattice.
 */
public final class CompleteMatchDiscovery {
    private final List<String> columns;
    private final List<int[]> rows = new ArrayList<>();
    private final List<BitSet> defined = new ArrayList<>();
    private final BigDecimal threshold;
    private final Map<List<Integer>, BitSet> masks = new HashMap<>();
    private long fdTested, uccTested, fdReported, uccReported;

    private CompleteMatchDiscovery(Path input, BigDecimal threshold) throws IOException {
        if (threshold.signum() <= 0 || threshold.compareTo(BigDecimal.ONE) > 0)
            throw new IllegalArgumentException("threshold must be in (0,1]");
        this.threshold = threshold;
        try (MaskedScope.Reader reader = new MaskedScope.Reader(input)) {
            columns = reader.columns;
            List<Map<String,Integer>> dictionaries = new ArrayList<>();
            for (int i = 0; i < columns.size(); i++) dictionaries.add(new HashMap<>());
            for (int i = 0; i < columns.size(); i++) defined.add(new BitSet());
            String[] row;
            while ((row = reader.next()) != null) {
                int[] encoded = new int[row.length];
                Arrays.fill(encoded, -1);
                for (int c = 0; c < row.length; c++) if (row[c] != null) {
                    defined.get(c).set(rows.size());
                    Map<String,Integer> dictionary = dictionaries.get(c);
                    encoded[c] = dictionary.computeIfAbsent(row[c], value -> dictionary.size());
                }
                rows.add(encoded);
            }
        }
    }
    private BitSet mask(List<Integer> selected) {
        return masks.computeIfAbsent(List.copyOf(selected), key -> {
            BitSet result = new BitSet(rows.size());
            result.set(0, rows.size());
            for (int c : key) result.and(defined.get(c));
            return result;
        });
    }
    private List<Integer> tuple(int[] row, List<Integer> selected) {
        List<Integer> key = new ArrayList<>(selected.size());
        for (int c : selected) key.add(row[c]);
        return key;
    }
    private boolean passes(long retained, long support) {
        return support > 0 && BigDecimal.valueOf(retained)
                .compareTo(threshold.multiply(BigDecimal.valueOf(support))) >= 0;
    }
    private String names(List<Integer> selected) {
        return String.join(", ", selected.stream().map(columns::get).toList());
    }
    private void evaluate(List<Integer> selected, BufferedWriter fds, BufferedWriter uccs,
                          BufferedWriter evidence) throws IOException {
        BitSet present = mask(selected);
        Set<List<Integer>> distinct = new HashSet<>();
        for (int r = present.nextSetBit(0); r >= 0; r = present.nextSetBit(r + 1))
            distinct.add(tuple(rows.get(r), selected));
        int support = present.cardinality();
        uccTested++;
        if (passes(distinct.size(), support)) {
            uccReported++;
            uccs.write("[" + names(selected) + "]#" + ((double) distinct.size() / support) + "\n");
            evidence.write("UCC\t" + selected + "\t-\t" + support + "\t" + distinct.size() + "\t" + rows.size() + "\n");
        }
        for (int y = 0; y < columns.size(); y++) {
            if (selected.contains(y)) continue;
            fdTested++;
            BitSet complete = (BitSet) present.clone();
            complete.and(defined.get(y));
            Map<List<Integer>, Map<Integer, Integer>> groups = new HashMap<>();
            for (int r = complete.nextSetBit(0); r >= 0; r = complete.nextSetBit(r + 1)) {
                int[] row = rows.get(r);
                groups.computeIfAbsent(tuple(row, selected), k -> new HashMap<>()).merge(row[y], 1, Integer::sum);
            }
            long retained = 0;
            for (Map<Integer, Integer> group : groups.values())
                retained += Collections.max(group.values());
            support = complete.cardinality();
            if (passes(retained, support)) {
                fdReported++;
                fds.write("[" + names(selected) + "]->" + columns.get(y) + "#" + ((double) retained / support) + "\n");
                evidence.write("FD\t" + selected + "\t" + y + "\t" + support + "\t" + retained + "\t" + rows.size() + "\n");
            }
        }
    }
    private void enumerate(List<Integer> selected, int next, int bound,
                           BufferedWriter fds, BufferedWriter uccs, BufferedWriter evidence) throws IOException {
        if (!selected.isEmpty()) evaluate(selected, fds, uccs, evidence);
        if (selected.size() == bound) return;
        for (int c = next; c < columns.size(); c++) {
            selected.add(c);
            enumerate(selected, c + 1, bound, fds, uccs, evidence);
            selected.remove(selected.size() - 1);
        }
    }
    public static void main(String[] args) throws Exception {
        if (args.length != 4) throw new IllegalArgumentException("input.mscope threshold maxArity outputPrefix");
        CompleteMatchDiscovery discovery = new CompleteMatchDiscovery(Path.of(args[0]), new BigDecimal(args[1]));
        int requested = Integer.parseInt(args[2]);
        if (requested != -1 && requested < 1) throw new IllegalArgumentException("maxArity must be positive or -1");
        int bound = requested == -1 ? discovery.columns.size() : Math.min(requested, discovery.columns.size());
        Path prefix = Path.of(args[3]);
        if (prefix.toAbsolutePath().getParent() != null) Files.createDirectories(prefix.toAbsolutePath().getParent());
        try (BufferedWriter fds = Files.newBufferedWriter(Path.of(prefix + ".fds"), StandardCharsets.UTF_8);
             BufferedWriter uccs = Files.newBufferedWriter(Path.of(prefix + ".uccs"), StandardCharsets.UTF_8);
             BufferedWriter evidence = Files.newBufferedWriter(Path.of(prefix + ".evidence.tsv"), StandardCharsets.UTF_8)) {
            evidence.write("kind\tcolumns_by_index\trhs_index\tcomplete_rows\tretained_rows\tscope_rows\n");
            discovery.enumerate(new ArrayList<>(), 0, bound, fds, uccs, evidence);
        }
        Files.writeString(Path.of(prefix + ".metadata.json"), String.format(Locale.ROOT,
                "{\"backend\":\"complete-match-v1\",\"output\":\"all-valid\",\"threshold\":%s,\"maxArity\":%d,\"columns\":%d,\"rows\":%d,\"fdTested\":%d,\"uccTested\":%d,\"fdReported\":%d,\"uccReported\":%d}\n",
                discovery.threshold.toPlainString(), bound, discovery.columns.size(), discovery.rows.size(),
                discovery.fdTested, discovery.uccTested, discovery.fdReported, discovery.uccReported));
    }

    /**
     * Runner-compatible entry point for masked inputs: bounded complete enumeration (arity from
     * {@code -Dmm.maxArity}, default 3), with the result copied to the requested dump path.
     */
    public static void runWrapper(String[] args, String kind) throws Exception {
        if (args.length < 5) throw new IllegalArgumentException("Masked discovery requires a dump path (plus its evidence and metadata)");
        String threshold = args.length > 3 ? args[3] : "1.0";
        String prefix = args[4] + ".complete-match";
        main(new String[]{args[1], threshold, System.getProperty("mm.maxArity", "3"), prefix});
        Files.copy(Path.of(prefix + "." + kind), Path.of(args[4]), StandardCopyOption.REPLACE_EXISTING);
        System.out.println("backend    : complete-match-v1; all-valid; maxArity=" + System.getProperty("mm.maxArity", "3"));
        System.out.println("dumped     : " + args[4]);
    }
}
