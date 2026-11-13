// Dump every function Ghidra knows: address, name, source (FID or analysis).
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import java.io.*;
public class DumpNames extends GhidraScript {
    public void run() throws Exception {
        PrintWriter w = new PrintWriter(new FileWriter("/home/claude/tools/ghidra_names.tsv"));
        for (Function f : currentProgram.getFunctionManager().getFunctions(true))
            w.println(f.getEntryPoint() + "\t" + f.getName() + "\t" + f.getSymbol().getSource());
        w.close();
    }
}
