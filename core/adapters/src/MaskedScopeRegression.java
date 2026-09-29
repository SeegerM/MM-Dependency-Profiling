package multimodel.adapters;

import java.nio.file.*;
import java.util.*;

/** Round-trip and malformed-input checks independent of dependency discovery. */
public final class MaskedScopeRegression {
    public static void main(String[] args) throws Exception {
        Path dir = Path.of(args[0]); Files.createDirectories(dir);
        List<String> names = List.of("empty", "missing", "literal", "unicode", "multiline");
        List<String> row = Arrays.asList("", null, "\\N,\"NULL\"", "  ü水  ", "a\r\nb\rc\nd");
        Path input = dir.resolve("roundtrip.mscope");
        try (MaskedScope.Writer writer = new MaskedScope.Writer(input,names)) {
            writer.row(row); writer.row(row);
        }
        try (MaskedScope.Reader reader = new MaskedScope.Reader(input)) {
            if (!reader.columns.equals(names)) throw new AssertionError("Header changed");
            for (int i=0;i<2;i++) if (!Arrays.asList(reader.next()).equals(row))
                throw new AssertionError("Value/mask/occurrence round trip failed");
            if (reader.next()!=null) throw new AssertionError("Extra occurrence");
        }
        String[] malformed = {
            "A,B\n1,2\n",
            "#multimodel-scope-v2,defined,A\n0,x,a\n",
            "#multimodel-scope-v2,defined,A\n0,0,not-empty\n",
            "#multimodel-scope-v2,defined,A\n1,1,a\n",
            "#multimodel-scope-v2,defined,A\n0,11,a\n",
            "#multimodel-scope-v2,defined,A,A\n",
            "#multimodel-scope-v2,defined,A\n0,1,a,extra\n"
        };
        for (int i=0;i<malformed.length;i++) {
            Path bad=dir.resolve("invalid-"+i+".mscope"); Files.writeString(bad,malformed[i]);
            boolean rejected=false;
            try (MaskedScope.Reader reader=new MaskedScope.Reader(bad)) { while(reader.next()!=null) {} }
            catch (java.io.IOException expected) { rejected=true; }
            if (!rejected) throw new AssertionError("Accepted malformed case "+i);
        }
        System.out.println("PASS: empty/null/literal/Unicode/whitespace/CRLF/duplicate occurrence roundtrip; 7 malformed inputs rejected");
    }
}
