import os
import sys
import traceback

def run_debug():
    report_path = r"c:\Users\admin\OneDrive\Máy tính\TOOL\debug_report.txt"
    dxf_path = r"c:\Users\admin\OneDrive\Máy tính\TOOL\FA26 MS TIEMPO MAESTRO CLUB FGMG J44+J45 100  9#.dxf"
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("=== DXF DEBUGLOG REPORT ===\n")
        f.write(f"Target file: {dxf_path}\n")
        
        # 1. Check file existence
        if not os.path.exists(dxf_path):
            f.write("ERROR: File does not exist at path!\n")
            print("Error: File not found.")
            return
            
        f.write(f"File size: {os.path.getsize(dxf_path) / 1024 / 1024:.2f} MB\n")
        
        # 2. Check ezdxf installation
        try:
            import ezdxf
            from ezdxf import recover
            f.write(f"ezdxf version: {ezdxf.__version__}\n")
        except ImportError:
            f.write("ERROR: ezdxf is not installed.\n")
            print("Error: ezdxf not installed.")
            return
            
        # 3. Attempt loading
        f.write("\n--- Attempting standard readfile ---\n")
        doc = None
        try:
            doc = ezdxf.readfile(dxf_path)
            f.write("SUCCESS: File loaded successfully with standard readfile.\n")
        except Exception as e:
            f.write(f"FAILED: Standard readfile failed.\nError: {str(e)}\n")
            f.write(traceback.format_exc())
            
            f.write("\n--- Attempting recover readfile ---\n")
            try:
                doc, auditor = recover.readfile(dxf_path)
                f.write(f"SUCCESS: File recovered with {len(auditor.errors)} errors.\n")
                for i, err in enumerate(auditor.errors[:10]):
                    f.write(f"  - Err {i}: {err.code} at {err.entity}\n")
            except Exception as rec_err:
                f.write(f"FAILED: Recover failed as well.\nError: {str(rec_err)}\n")
                f.write(traceback.format_exc())
                print("Failed both load attempts.")
                
        if doc is not None:
            try:
                if hasattr(doc, "model_space"):
                    msp = doc.model_space()
                else:
                    msp = doc.modelspace()
                f.write(f"\nTotal entities in Model Space: {len(msp)}\n")
                
                # Count entities by layer
                layers = {}
                for ent in msp:
                    lay = ent.dxf.layer
                    layers[lay] = layers.get(lay, 0) + 1
                    
                f.write("\nLayers & Entity Counts:\n")
                for lay, count in sorted(layers.items(), key=lambda x: x[1], reverse=True):
                    f.write(f" - Layer '{lay}': {count} entities\n")
                    
                f.write("\nEntity Type Distribution:\n")
                types = {}
                for ent in msp:
                    t = ent.dxftype()
                    types[t] = types.get(t, 0) + 1
                for t, count in sorted(types.items(), key=lambda x: x[1], reverse=True):
                    f.write(f" - {t}: {count}\n")
                    
                print(f"Successfully analyzed. Output written to {report_path}")
            except Exception as parse_err:
                f.write(f"\nERROR during model space parsing: {str(parse_err)}\n")
                f.write(traceback.format_exc())

if __name__ == "__main__":
    run_debug()
