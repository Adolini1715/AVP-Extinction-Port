// Create a function at every start the lifter knows (starts.txt), before auto-analysis.
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import java.nio.file.*;
public class SeedStarts extends GhidraScript {
    public void run() throws Exception {
        int n = 0;
        for (String l : Files.readAllLines(Paths.get("/home/claude/tools/starts.txt"))) {
            Address a = toAddr(Long.decode(l.trim()));
            disassemble(a);
            if (getFunctionAt(a) == null && createFunction(a, null) != null) n++;
        }
        println("seeded " + n);
    }
}
