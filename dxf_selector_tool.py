import os
import sys
import math
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Define global default path for the user's file
DEFAULT_DXF_PATH = r"c:\Users\admin\OneDrive\Máy tính\TOOL\FA26 MS TIEMPO MAESTRO CLUB FGMG J44+J45 100  9#.dxf"

try:
    import ezdxf
    from ezdxf import path, recover
except ImportError:
    pass

try:
    import numpy as np
    import matplotlib
    matplotlib.use('TkAgg')
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.collections import LineCollection
    from matplotlib.widgets import RectangleSelector
except ImportError:
    pass

try:
    from PIL import Image, ImageDraw
except ImportError:
    pass


class DXFProcessorApp:
    def __init__(self, root):
        self.root = root
        
        # ABSOLUTE PRE-INIT LIBRARY VERIFICATION (Avoid NameError crashes)
        missing = []
        try: import ezdxf
        except ImportError: missing.append("ezdxf")
        try: import numpy
        except ImportError: missing.append("numpy")
        try: import matplotlib
        except ImportError: missing.append("matplotlib")
        try: import PIL
        except ImportError: missing.append("pillow")
        
        if missing:
            from tkinter import messagebox
            msg = f"⚠️ THIẾU THƯ VIỆN CHẠY ỨNG DỤNG!\n\n" \
                  f"Máy anh chưa cài đặt hoặc đang chạy trong môi trường Python thiếu thư viện sau:\n" \
                  f"➡️ {', '.join(missing)}\n\n" \
                  f"Anh hãy mở cmd gõ dòng sau để cài đặt tất cả:\n" \
                  f"pip install {' '.join(missing)}"
            messagebox.showerror("Lỗi Thiếu Thư Viện", msg)
            sys.exit(1) # Exit clean
            
        self.root.title("DXF Smart Selector & Mask Generator v2 (High-Perf & Auto-Recovery)")
        self.root.geometry("1350x850")
        
        # Set visual styles
        self.setup_styles()
        
        # Engine state
        self.doc = None
        self.msp = None
        self.filepath = None
        self.layers = []
        self.selected_layers = set()
        
        # Geometry Cache
        self.layer_geometry = {} 
        
        # Selection Bounds
        self.selection_box = None 
        
        self.build_ui()
        
        # Library check & Auto-loader
        self.root.after(100, self.check_libs_and_autoload)

    def setup_styles(self):
        self.style = ttk.Style()
        if sys.platform == "win32":
            self.style.theme_use('vista')
        
        self.style.configure("TButton", font=("Segoe UI", 10), padding=5)
        self.style.configure("Header.TLabel", font=("Segoe UI Semibold", 12), foreground="#1a73e8")
        self.style.configure("Status.TLabel", font=("Segoe UI", 9, "bold"), relief=tk.SUNKEN, anchor=tk.W)

    def build_ui(self):
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(1, weight=1)
        
        # --- Top Panel ---
        top_frame = ttk.Frame(self.root, padding=10, relief=tk.RAISED)
        top_frame.grid(row=0, column=0, columnspan=3, sticky="ew")
        
        self.btn_load = ttk.Button(top_frame, text="📁 Chọn Mở File DXF Mới", command=self.load_dxf_dialog)
        self.btn_load.pack(side=tk.LEFT, padx=5)
        
        self.lbl_file_info = ttk.Label(top_frame, text="Chờ tải file...", font=("Segoe UI", 10, "bold"))
        self.lbl_file_info.pack(side=tk.LEFT, padx=20)
        
        # --- Left Side: Layer Management ---
        left_frame = ttk.LabelFrame(self.root, text=" 📂 Quản lý Layers ", padding=10)
        left_frame.grid(row=1, column=0, sticky="nsw", padx=10, pady=10)
        
        # Search tools
        filter_frame = ttk.Frame(left_frame)
        filter_frame.pack(fill=tk.X, pady=(0,5))
        ttk.Label(filter_frame, text="Lọc:").pack(side=tk.LEFT, padx=(0, 5))
        self.entry_search = ttk.Entry(filter_frame, width=15)
        self.entry_search.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry_search.bind("<KeyRelease>", self.filter_layer_listbox)
        
        ttk.Button(filter_frame, text="Nike Check", command=self.quick_filter_nike, width=11).pack(side=tk.RIGHT, padx=(5,0))
        
        # The Listbox
        list_container = ttk.Frame(left_frame)
        list_container.pack(fill=tk.BOTH, expand=True)
        
        self.layer_listbox = tk.Listbox(list_container, selectmode=tk.MULTIPLE, width=32, font=("Consolas", 10), exportselection=False)
        self.layer_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.layer_listbox.bind('<<ListboxSelect>>', self.on_layer_listbox_change)
        
        scrollbar = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.layer_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.layer_listbox.config(yscrollcommand=scrollbar.set)
        
        btn_sel_frame = ttk.Frame(left_frame, padding=(0, 5))
        btn_sel_frame.pack(fill=tk.X)
        ttk.Button(btn_sel_frame, text="Chọn hết", command=self.select_all_layers).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(btn_sel_frame, text="Bỏ chọn hết", command=self.deselect_all_layers).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        
        self.btn_draw = ttk.Button(left_frame, text="🔄 Cập nhật Preview Hình", command=self.render_preview)
        self.btn_draw.pack(fill=tk.X, pady=(10,0))

        # --- Center: Visual Canvas ---
        center_frame = ttk.LabelFrame(self.root, text=" 👁️ Trình xem vẽ (Bản đồ Vector) ")
        center_frame.grid(row=1, column=1, sticky="nsew", pady=10)
        
        # Initialize matplot canvas
        self.fig, self.ax = plt.subplots(figsize=(8, 6))
        self.fig.subplots_adjust(left=0.02, right=0.98, top=0.98, bottom=0.02)
        self.ax.set_aspect('equal')
        self.ax.axis('off')
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=center_frame)
        
        # PREMIUM USER EXPERIENCE: Bind instant CAD interaction events
        self.canvas.mpl_connect('scroll_event', self.on_mouse_scroll_zoom)
        self.canvas.mpl_connect('button_press_event', self.on_mouse_press)
        self.canvas.mpl_connect('button_release_event', self.on_mouse_release)
        self.canvas.mpl_connect('motion_notify_event', self.on_mouse_motion)
        
        self.pan_press_active = False
        
        # Toolbar setup
        self.toolbar_frame = ttk.Frame(center_frame)
        self.toolbar_frame.pack(fill=tk.X, side=tk.TOP)
        self.toolbar = NavigationToolbar2Tk(self.canvas, self.toolbar_frame)
        self.toolbar.update()
        
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, side=tk.TOP)
        
        # Advanced Selector Tool
        self.rs = RectangleSelector(
            self.ax, self.on_rect_select,
            useblit=True,
            button=[1],
            minspanx=5, minspany=5,
            interactive=True,
            props=dict(facecolor='#0d6efd', edgecolor='#0b5ed7', alpha=0.3, fill=True)
        )
        self.rs.set_active(False)

        self.btn_toggle_sel = ttk.Button(self.toolbar_frame, text="🎯 BẬT RÊ CHUỘT CHỌN VÙNG", command=self.toggle_selection_tool)
        self.btn_toggle_sel.pack(side=tk.LEFT, padx=15, pady=2)
        
        self.btn_clear_sel = ttk.Button(self.toolbar_frame, text="❌ Bỏ vùng chọn", command=self.clear_rect_selection)
        self.btn_clear_sel.pack(side=tk.LEFT, padx=2, pady=2)

        # --- Right Side: Operations panel ---
        right_frame = ttk.LabelFrame(self.root, text=" ⚡ Chức năng Xử lý & Xuất file ", padding=10)
        right_frame.grid(row=1, column=2, sticky="nsew", padx=10, pady=10)
        
        # DXF Saver Box
        dxf_box = ttk.LabelFrame(right_frame, text=" 💾 XUẤT FILE DXF MỚI ", padding=10)
        dxf_box.pack(fill=tk.X, pady=(0, 15))
        self.lbl_selection_status = ttk.Label(dxf_box, text="Vùng chọn: Chưa khoanh (Lấy tất cả layers đang hiện)")
        self.lbl_selection_status.pack(anchor=tk.W, pady=(0, 10))
        ttk.Button(dxf_box, text="LƯU THÀNH FILE DXF KHÁC", command=self.export_to_dxf).pack(fill=tk.X)
        
        # Image Mask generator Box
        mask_box = ttk.LabelFrame(right_frame, text=" 🖼️ XUẤT ẢNH MASK TRẮNG ĐEN ", padding=10)
        mask_box.pack(fill=tk.X)
        
        ttk.Label(mask_box, text="Độ phân giải chiều ngang (pixels):").pack(anchor=tk.W, pady=(5,2))
        self.var_mask_width = tk.StringVar(value="2500")
        ttk.Entry(mask_box, textvariable=self.var_mask_width).pack(fill=tk.X, pady=(0,10))
        
        ttk.Label(mask_box, text="Kiểu tô màu Mask:").pack(anchor=tk.W, pady=(5,2))
        self.var_mask_mode = tk.StringVar(value="outline")
        ttk.Radiobutton(mask_box, text="Chỉ vẽ đường viền nét (Outline)", variable=self.var_mask_mode, value="outline").pack(anchor=tk.W)
        ttk.Radiobutton(mask_box, text="Tô trắng đặc khối (Filled)", variable=self.var_mask_mode, value="filled").pack(anchor=tk.W)
        
        ttk.Label(mask_box, text="Độ rộng nét viền (pixel):").pack(anchor=tk.W, pady=(10,2))
        self.var_thickness = tk.StringVar(value="6")
        ttk.Entry(mask_box, textvariable=self.var_thickness).pack(fill=tk.X, pady=(0,15))
        
        ttk.Button(mask_box, text="TẠO VÀ LƯU ẢNH PNG MASK", command=self.export_to_mask).pack(fill=tk.X)
        
        # Guide instructions box
        guide_box = ttk.LabelFrame(right_frame, text=" 💡 Mẹo sử dụng file 38MB ", padding=10)
        guide_box.pack(fill=tk.BOTH, expand=True, pady=(15, 0))
        guide_text = (
            "• File của anh RẤT NẶNG (38MB). Không nên bật HẾT Layers một lúc để tránh đứng máy.\n"
            "• Chương trình đã tự động lọc các Layers tiềm năng của Nike.\n"
            "• Chỉ tích chọn Layer cần vẽ rồi ấn 'Cập nhật Preview' để xem.\n"
            "• Bật 'Rê chuột chọn vùng' và khoanh riêng cái Logo Nike để xuất riêng siêu nhanh."
        )
        ttk.Label(guide_box, text=guide_text, justify=tk.LEFT, wraplength=240).pack(anchor=tk.W)

        # --- Footer Progress ---
        self.status_bar = ttk.Label(self.root, text="Chương trình đã sẵn sàng.", style="Status.TLabel")
        self.status_bar.grid(row=2, column=0, columnspan=3, sticky="ew")

    def set_status(self, text):
        self.status_bar.config(text=f" STATUS: {text}")
        self.root.update_idletasks()

    def check_libs_and_autoload(self):
        missing = []
        try: import ezdxf
        except ImportError: missing.append("ezdxf")
        try: import numpy
        except ImportError: missing.append("numpy")
        try: import matplotlib
        except ImportError: missing.append("matplotlib")
        try: import PIL
        except ImportError: missing.append("pillow")
        
        if missing:
            msg = f"Hãy cài đặt thư viện để chạy chương trình:\n\npip install {' '.join(missing)}"
            messagebox.showerror("Lỗi thư viện", msg)
            return

        # Smart ezdxf version check
        try:
            import ezdxf
            v_str = getattr(ezdxf, "__version__", "0.0.0")
            v_parts = []
            for p in v_str.split("."):
                clean_p = "".join(filter(str.isdigit, p))
                if clean_p: v_parts.append(int(clean_p))
            
            if len(v_parts) >= 2 and (v_parts[0] == 0 and v_parts[1] < 16):
                msg = f"⚡ PHÁT HIỆN PHIÊN BẢN EZDXF CŨ ({v_str})\n\n" \
                      f"Thư viện CAD trên máy anh đang ở bản quá cũ, thiếu các thuật toán xử lý hình học hiện đại (như spline, block).\n\n" \
                      f"Anh hãy mở cmd và dán dòng sau để NÂNG CẤP LÊN BẢN MỚI NHẤT:\n" \
                      f"pip install -U ezdxf matplotlib numpy pillow\n\n" \
                      f"Chương trình sẽ thử chạy tiếp với chế độ tương thích ngược!"
                messagebox.showwarning("Nên nâng cấp thư viện", msg)
        except Exception:
            pass

        # Trigger Autoload
        if os.path.exists(DEFAULT_DXF_PATH):
            print(f"Found default file at startup: {DEFAULT_DXF_PATH}")
            self.filepath = DEFAULT_DXF_PATH
            self.set_status(f"Phát hiện file DXF mặc định. Đang tự động nạp dữ liệu...")
            self.root.after(200, self.process_dxf_file)
        else:
            self.set_status("Không thấy file mặc định. Vui lòng chọn nút 'Mở File DXF' để chọn file.")
            print(f"Default file not found at: {DEFAULT_DXF_PATH}")

    def load_dxf_dialog(self):
        fpath = filedialog.askopenfilename(filetypes=[("CAD Files", "*.dxf")])
        if fpath:
            self.filepath = fpath
            self.process_dxf_file()

    def process_dxf_file(self):
        self.set_status(f"Đang giải mã cấu trúc file {os.path.basename(self.filepath)}...")
        self.lbl_file_info.config(text=f"Đang nạp: {os.path.basename(self.filepath)}")
        self.root.update()
        
        # Reset old variables
        self.doc = None
        self.msp = None
        self.layer_geometry.clear()
        self.selected_layers.clear()
        
        try:
            print(f"Attempting to read DXF: {self.filepath}")
            # Try standard read first
            try:
                self.doc = ezdxf.readfile(self.filepath)
                print("Load success with standard reader.")
            except Exception as read_err:
                print(f"Standard load failed: {read_err}. Retrying with ezdxf.recover...")
                self.set_status("Cấu trúc file lỗi nhẹ, đang kích hoạt chế độ TỰ SỬA LỖI (Recover mode)...")
                self.root.update()
                # Advanced Recovery loading
                self.doc, auditor = recover.readfile(self.filepath)
                print(f"Recover reader completed with {len(auditor.errors)} errors repaired.")
            
            # Robust fallback for both model_space() and modelspace() depending on ezdxf version
            if hasattr(self.doc, "model_space"):
                self.msp = self.doc.model_space()
            else:
                self.msp = self.doc.modelspace()
            self.layers = sorted(list(set(ent.dxf.layer for ent in self.msp)))
            
            # Output to console
            print(f"Layers count: {len(self.layers)}. Entities count: {len(self.msp)}")
            
            self.lbl_file_info.config(text=f"Tệp: {os.path.basename(self.filepath)} | {len(self.msp)} Objects | {len(self.layers)} Layers")
            
            # Render visible layer listbox
            self.update_layer_listbox_display(self.layers)
            
            # --- CRITICAL OPTIMIZATION ---
            # DO NOT select all layers. Search for potential NIKE Layers automatically to limit size.
            keywords = ["nike", "logo", "swoosh", "symbol", "pattern"]
            auto_hits = [l for l in self.layers if any(kw in l.lower() for kw in keywords)]
            
            if auto_hits:
                self.set_status(f"Nạp thành công! Đã tự phát hiện {len(auto_hits)} layers liên quan đến Nike.")
                self.selected_layers = set(auto_hits)
                for i in range(self.layer_listbox.size()):
                    if self.layer_listbox.get(i) in auto_hits:
                        self.layer_listbox.selection_set(i)
                # Trigger immediate preview render for just THESE auto-selected layers
                self.root.after(300, self.render_preview)
            else:
                self.set_status("Đã nạp cấu trúc. Vui lòng chọn Layer cần xem ở cột trái rồi nhấn Cập nhật Preview.")
                
        except Exception as e:
            err_trace = traceback.format_exc()
            print(err_trace)
            messagebox.showerror("Lỗi nghiêm trọng", f"Không thể phân tích file DXF này.\n\nChi tiết lỗi:\n{e}")
            self.set_status("Thất bại.")

    def update_layer_listbox_display(self, layers_list):
        self.layer_listbox.delete(0, tk.END)
        for idx, l in enumerate(layers_list):
            self.layer_listbox.insert(tk.END, l)
            if l in self.selected_layers:
                self.layer_listbox.selection_set(idx)

    def filter_layer_listbox(self, event=None):
        term = self.entry_search.get().lower()
        # Save current list selection states before filter
        current_cursel = self.layer_listbox.curselection()
        for i in range(self.layer_listbox.size()):
            val = self.layer_listbox.get(i)
            if i in current_cursel:
                self.selected_layers.add(val)
            else:
                self.selected_layers.discard(val)
                
        filtered = [l for l in self.layers if term in l.lower()]
        self.update_layer_listbox_display(filtered)

    def quick_filter_nike(self):
        keywords = ["nike", "logo", "swoosh", "symbol"]
        self.entry_search.delete(0, tk.END)
        self.entry_search.insert(0, "nike")
        filtered = [l for l in self.layers if any(k in l.lower() for k in keywords)]
        
        if filtered:
            self.selected_layers = set(filtered)
            self.update_layer_listbox_display(filtered)
            # Select them visually
            for i in range(self.layer_listbox.size()):
                self.layer_listbox.selection_set(i)
            self.render_preview()
        else:
            messagebox.showinfo("Không tìm thấy", "Không có layer nào trùng tên khóa 'nike'.")
            self.entry_search.delete(0, tk.END)
            self.update_layer_listbox_display(self.layers)

    def on_layer_listbox_change(self, event):
        # Re-sync selection tracking
        box_sel = set(self.layer_listbox.get(i) for i in self.layer_listbox.curselection())
        for i in range(self.layer_listbox.size()):
            val = self.layer_listbox.get(i)
            if val in box_sel:
                self.selected_layers.add(val)
            else:
                self.selected_layers.discard(val)

    def select_all_layers(self):
        for i in range(self.layer_listbox.size()):
            self.layer_listbox.selection_set(i)
            self.selected_layers.add(self.layer_listbox.get(i))

    def deselect_all_layers(self):
        self.layer_listbox.selection_clear(0, tk.END)
        for i in range(self.layer_listbox.size()):
            self.selected_layers.discard(self.layer_listbox.get(i))

    # --- Efficient Geometry Loader (Asynchronous GUI & Ultra-compatible Fallback) ---
    def extract_paths_recursive(self, entity, layer_name):
        paths_found = []
        t = entity.dxftype()
        
        if t == 'INSERT':
            try:
                for sub in entity.virtual_entities():
                    paths_found.extend(self.extract_paths_recursive(sub, layer_name))
            except Exception: 
                pass
            return paths_found
            
        # --- STRATEGY 1: Try modern ezdxf.path module (Fast & Perfect) ---
        try:
            # Check if path global was successfully imported and has make_paths_from_entities
            if "path" in globals() and hasattr(path, "make_paths_from_entities"):
                ent_paths = path.make_paths_from_entities([entity])
                for p in ent_paths:
                    coords = np.array(list(p.flatten(distance=0.25)))
                    if len(coords) > 1:
                        paths_found.append({
                            'coords': coords,
                            'layer': layer_name,
                            'entity': entity
                        })
                if len(paths_found) > 0:
                    return paths_found
        except Exception as path_err:
            print(f"Modern path module exception on {t}: {path_err}")
            # Fall through to legacy parser
            
        # --- STRATEGY 2: Legacy Manual Parser (Bulletproof Compatibility fallback) ---
        try:
            coords = []
            
            if t == 'LINE':
                # Line start and end
                coords = [entity.dxf.start[:2], entity.dxf.end[:2]]
                
            elif t == 'LWPOLYLINE':
                # Modern and older ezdxf both support get_points()
                raw_pts = list(entity.get_points())
                # Strip format down to x,y
                coords = [p[:2] for p in raw_pts]
                
            elif t == 'POLYLINE':
                # Traditional heavy polylines
                coords = [v.dxf.location[:2] for v in entity.vertices]
                
            elif t == 'ARC':
                # Calculate standard arc segments manually
                center = entity.dxf.center[:2]
                r = entity.dxf.radius
                sa = math.radians(entity.dxf.start_angle)
                ea = math.radians(entity.dxf.end_angle)
                if ea < sa: ea += 2 * math.pi
                
                # Number of interpolation segments depends on arc size
                steps = max(8, int((ea - sa) * 8)) 
                angles = np.linspace(sa, ea, steps)
                coords = [[center[0] + r*math.cos(a), center[1] + r*math.sin(a)] for a in angles]
                
            elif t == 'CIRCLE':
                # 32-segment circle manual interpolation
                center = entity.dxf.center[:2]
                r = entity.dxf.radius
                angles = np.linspace(0, 2 * math.pi, 32)
                coords = [[center[0] + r*math.cos(a), center[1] + r*math.sin(a)] for a in angles]
                
            elif t == 'SPLINE':
                # If spline flattening failed, fall back to control points or fit points
                if hasattr(entity, "control_points") and len(entity.control_points) > 0:
                    coords = [cp[:2] for cp in entity.control_points]
                elif hasattr(entity, "fit_points") and len(entity.fit_points) > 0:
                    coords = [fp[:2] for fp in entity.fit_points]
                    
            if len(coords) > 1:
                paths_found.append({
                    'coords': np.array(coords),
                    'layer': layer_name,
                    'entity': entity
                })
        except Exception as legacy_err:
            # Ignore failures on unsupported exotic entity types (like MTEXT, POINT, etc.)
            pass
            
        return paths_found

    def load_selected_geometry_cache(self):
        needed = [l for l in self.selected_layers if l not in self.layer_geometry]
        if not needed:
            return True
            
        total_needed = len(needed)
        print(f"Caching geometry for {total_needed} new layers...")
        
        for i, l in enumerate(needed):
            # Keep GUI alive by yielding window tasks
            self.set_status(f"Đang phân tích Layer ({i+1}/{total_needed}): {l}...")
            self.root.update()
            
            layer_ents = [ent for ent in self.msp if ent.dxf.layer == l]
            self.layer_geometry[l] = []
            
            for count, ent in enumerate(layer_ents):
                # Break long operations every 200 entities to prevent "Not Responding"
                if count % 200 == 0 and count > 0:
                    self.set_status(f"Đang trích xuất hình khối Layer '{l}': {count}/{len(layer_ents)} entities...")
                    self.root.update()
                    
                extracted = self.extract_paths_recursive(ent, l)
                self.layer_geometry[l].extend(extracted)
                
        self.set_status("Trích xuất hoàn tất.")
        return True

    # --- Rendering ---
    def render_preview(self):
        if not self.msp:
            return
            
        self.set_status("Đang dựng hình ảnh preview... Vui lòng chờ.")
        self.ax.clear()
        self.on_layer_listbox_change(None)
        
        if not self.selected_layers:
            self.ax.text(0.5, 0.5, "Chưa chọn Layer nào.", ha='center', transform=self.ax.transAxes)
            self.canvas.draw()
            self.set_status("Đã vẽ rỗng.")
            return
            
        # Cache calculation with updates
        success = self.load_selected_geometry_cache()
        if not success: return
        
        self.set_status("Đang dán dữ liệu vector lên canvas...")
        self.root.update()
        
        segments = []
        for l in self.selected_layers:
            for item in self.layer_geometry.get(l, []):
                segments.append(item['coords'])
                
        if segments:
            # Heavy performance optimization: Group all paths into single LineCollection
            lc = LineCollection(segments, colors='#000000', linewidths=0.7, antialiaseds=True)
            self.ax.add_collection(lc)
            
            # PREMIUM LIVE SELECTION OVERLAY: High-contrast red highlight collection stacked on top
            self.lc_highlight = LineCollection([], colors='#d93025', linewidths=1.7, zorder=15)
            self.ax.add_collection(self.lc_highlight)
            
            # Cache current render segments for interactive callbacks
            self.current_canvas_segments = segments
            
            self.ax.autoscale_view()
            all_stacked = np.vstack(segments)
            xmin, ymin = np.min(all_stacked, axis=0)
            xmax, ymax = np.max(all_stacked, axis=0)
            
            # Set plot margins
            dx, dy = xmax-xmin, ymax-ymin
            margin = max(dx, dy) * 0.05
            self.ax.set_xlim(xmin - margin, xmax + margin)
            self.ax.set_ylim(ymin - margin, ymax + margin)
            self.ax.set_aspect('equal')
        else:
            self.ax.text(0.5, 0.5, "Layers đã chọn không chứa hình vẽ vector hợp lệ.", ha='center', transform=self.ax.transAxes)
            
        self.ax.axis('off')
        self.canvas.draw()
        self.set_status(f"Đã hiển thị xong {len(segments)} đường cong.")

    # --- Region Selector Logic ---
    def toggle_selection_tool(self):
        if not self.rs.get_active():
            self.rs.set_active(True)
            self.btn_toggle_sel.config(text="✅ BẬT RÊ CHUỘT", style="Accent.TButton")
            self.set_status("RÊ CHUỘT TRÁI ĐỂ KHOANH VÙNG. Kéo trực tiếp trên hình vẽ để chọn vùng.")
        else:
            self.rs.set_active(False)
            self.btn_toggle_sel.config(text="🎯 BẬT RÊ CHUỘT CHỌN VÙNG")
            self.set_status("Đã tắt chế độ chọn.")

    def clear_rect_selection(self):
        self.selection_box = None
        self.rs.clear()
        self.rs.set_active(False)
        self.btn_toggle_sel.config(text="🎯 BẬT RÊ CHUỘT CHỌN VÙNG")
        self.lbl_selection_status.config(text="Vùng chọn: Chưa khoanh (Lấy tất cả layers đang hiện)")
        
        # Visually clear highlighters instantly
        if hasattr(self, "lc_highlight"):
            self.lc_highlight.set_segments([])
            
        self.canvas.draw_idle()
        self.set_status("Đã hủy vùng chọn.")

    def on_rect_select(self, eclick, erelease):
        x1, y1 = eclick.xdata, eclick.ydata
        x2, y2 = erelease.xdata, erelease.ydata
        
        if x1 is None or x2 is None: return
        
        xmin, xmax = sorted([x1, x2])
        ymin, ymax = sorted([y1, y2])
        
        self.selection_box = (xmin, xmax, ymin, ymax)
        self.lbl_selection_status.config(text=f"VÙNG ĐÃ CHỌN: X[{xmin:.1f} -> {xmax:.1f}]")
        self.set_status(f"Đã khoanh vùng: X:[{xmin:.2f}, {xmax:.2f}] Y:[{ymin:.2f}, {ymax:.2f}]")
        
        # LIVE RENDERING: Update user selection highlight instantly
        self.refresh_visual_selections()

    # --- Bulletproof Geometry Line-Box Intersection Algorithm ---
    
    def is_path_intersecting_box(self, coords, xmin, xmax, ymin, ymax):
        """Highly optimized NumPy intersection checker that accurately selects crossing lines."""
        if coords is None or len(coords) < 2:
            return False
            
        # TEST 1: Check if any endpoint/vertex is strictly inside the selection box (Fastest check)
        in_x = (coords[:, 0] >= xmin) & (coords[:, 0] <= xmax)
        in_y = (coords[:, 1] >= ymin) & (coords[:, 1] <= ymax)
        if np.any(in_x & in_y):
            return True
            
        # TEST 2: Segment-Box Crossings (If outline crosses box but endpoints are outside)
        x1 = coords[:-1, 0]
        y1 = coords[:-1, 1]
        x2 = coords[1:, 0]
        y2 = coords[1:, 1]
        
        # Vectorized reject: check if bounding-box of each segment overlaps query box
        seg_xmin = np.minimum(x1, x2)
        seg_xmax = np.maximum(x1, x2)
        seg_ymin = np.minimum(y1, y2)
        seg_ymax = np.maximum(y1, y2)
        
        overlap = (seg_xmin <= xmax) & (seg_xmax >= xmin) & (seg_ymin <= ymax) & (seg_ymax >= ymin)
        if not np.any(overlap):
            return False
            
        # Run precise parametric line clip (Liang-Barsky derived) for candidate segments
        idx = np.where(overlap)[0]
        ox1, oy1 = x1[idx], y1[idx]
        ox2, oy2 = x2[idx], y2[idx]
        
        dx = ox2 - ox1
        dy = oy2 - oy1
        
        with np.errstate(divide='ignore', invalid='ignore'):
            # Parametric entry/exit values across 4 infinite lines defining the box sides
            t_left  = np.where(dx != 0, (xmin - ox1) / dx, np.where(ox1 >= xmin, -np.inf, np.inf))
            t_right = np.where(dx != 0, (xmax - ox1) / dx, np.where(ox1 <= xmax, np.inf, -np.inf))
            t_bot   = np.where(dy != 0, (ymin - oy1) / dy, np.where(oy1 >= ymin, -np.inf, np.inf))
            t_top   = np.where(dy != 0, (ymax - oy1) / dy, np.where(oy1 <= ymax, np.inf, -np.inf))
            
        tmin_x = np.minimum(t_left, t_right)
        tmax_x = np.maximum(t_left, t_right)
        tmin_y = np.minimum(t_bot, t_top)
        tmax_y = np.maximum(t_bot, t_top)
        
        # Max entry and min exit times on the interval [0, 1]
        t_enter = np.maximum(np.maximum(tmin_x, tmin_y), 0.0)
        t_exit  = np.minimum(np.minimum(tmax_x, tmax_y), 1.0)
        
        # Overlap occurs if the interval [t_enter, t_exit] is non-empty
        return np.any(t_enter <= t_exit)

    def refresh_visual_selections(self):
        """Re-evaluate highlights on cached paths and redraw canvas instantly."""
        if not hasattr(self, "lc_highlight") or not hasattr(self, "current_canvas_segments") or not self.selection_box:
            return
            
        xmin, xmax, ymin, ymax = self.selection_box
        highlight_segments = []
        
        for seg in self.current_canvas_segments:
            if self.is_path_intersecting_box(seg, xmin, xmax, ymin, ymax):
                highlight_segments.append(seg)
                
        self.lc_highlight.set_segments(highlight_segments)
        self.canvas.draw_idle()

    # --- High-Performance Interaction Event Handlers (CAD Zoom/Pan) ---
    
    def on_mouse_scroll_zoom(self, event):
        """Ultra-stable CAD scroll-wheel zoom locking position perfectly to the mouse pointer."""
        if event.inaxes != self.ax or not self.msp:
            return
            
        # Static Zoom increment scale
        base_scale = 1.25
        
        if event.button == 'up':
            scale_factor = 1.0 / base_scale
        elif event.button == 'down':
            scale_factor = base_scale
        else:
            if hasattr(event, 'step') and event.step != 0:
                scale_factor = 1.0 / base_scale if event.step > 0 else base_scale
            else:
                return

        # Get absolute anchor coordinate under cursor
        xdata, ydata = event.xdata, event.ydata
        cur_xlim = self.ax.get_xlim()
        cur_ylim = self.ax.get_ylim()
        
        # Dynamic limits vector bounding - GUARANTEES perfect origin/cursor lock
        dist_left   = xdata - cur_xlim[0]
        dist_right  = cur_xlim[1] - xdata
        dist_bottom = ydata - cur_ylim[0]
        dist_top    = cur_ylim[1] - ydata
        
        # Prevent excessive zoom-out breaking the display engine
        if scale_factor > 1.0 and (dist_left + dist_right) > 5e7:
            return 

        # Compute perfectly scaled viewport bounds
        self.ax.set_xlim([xdata - dist_left * scale_factor, xdata + dist_right * scale_factor])
        self.ax.set_ylim([ydata - dist_bottom * scale_factor, ydata + dist_top * scale_factor])
        
        self.canvas.draw_idle()

    def on_mouse_press(self, event):
        """Capture starting anchor points when right/middle clicking to Pan."""
        if event.inaxes != self.ax or not self.msp:
            return
            
        # Use Button 3 (Right Mouse Button) or Button 2 (Middle Click) for Panning
        if event.button in [2, 3]:
            self.pan_press_active = True
            self.pan_start_x = event.x
            self.pan_start_y = event.y
            self.pan_xlim = self.ax.get_xlim()
            self.pan_ylim = self.ax.get_ylim()

    def on_mouse_release(self, event):
        """Deactivate Panning mode."""
        if event.button in [2, 3]:
            self.pan_press_active = False

    def on_mouse_motion(self, event):
        """Recompute limits interactively when dragging to pan."""
        if not getattr(self, 'pan_press_active', False) or not self.msp:
            return
        if event.x is None or event.y is None:
            return
            
        # Compute displacement in pixels
        dx_px = event.x - self.pan_start_x
        dy_px = event.y - self.pan_start_y
        
        # Use coordinate transform inverter to convert screen delta back into CAD space coords
        trans = self.ax.transData.inverted()
        p0 = trans.transform((0, 0))
        p1 = trans.transform((dx_px, dy_px))
        
        dx_data = p1[0] - p0[0]
        dy_data = p1[1] - p0[1]
        
        # Apply offset transformation
        self.ax.set_xlim(self.pan_xlim[0] - dx_data, self.pan_xlim[1] - dx_data)
        self.ax.set_ylim(self.pan_ylim[0] - dy_data, self.pan_ylim[1] - dy_data)
        
        self.canvas.draw_idle()

    def get_filtered_dataset(self):
        if not self.selected_layers:
            return [], []
            
        self.load_selected_geometry_cache()
        
        selected_entities = set()
        selected_paths = []
        
        box = self.selection_box
        
        for l in self.selected_layers:
            for item in self.layer_geometry.get(l, []):
                coords = item['coords']
                
                match = False
                if box is None:
                    match = True
                else:
                    # Robust segment-intersection check!
                    xmin, xmax, ymin, ymax = box
                    if self.is_path_intersecting_box(coords, xmin, xmax, ymin, ymax):
                        match = True
                        
                if match:
                    selected_entities.add(item['entity'])
                    selected_paths.append(coords)
                    
        return list(selected_entities), selected_paths

    # --- Export & Save Functions ---
    def export_to_dxf(self):
        if not self.doc: return
        
        ents, _ = self.get_filtered_dataset()
        if not ents:
            messagebox.showwarning("Trống", "Không tìm thấy hình vẽ nào khớp trong vùng đã chọn!")
            return
            
        save_path = filedialog.asksaveasfilename(
            defaultextension=".dxf",
            filetypes=[("CAD Files", "*.dxf")],
            initialfile="ket_qua_chon_nike.dxf"
        )
        
        if not save_path: return
        
        self.set_status("Đang đóng gói và xuất file DXF mới...")
        try:
            new_doc = ezdxf.new(dxfversion=self.doc.dxfversion)
            # Robust fallback for newer and older ezdxf
            if hasattr(new_doc, "model_space"):
                new_msp = new_doc.model_space()
            else:
                new_msp = new_doc.modelspace()
            
            # Copy layers properties
            for old_lay in self.doc.layers:
                if old_lay.dxf.name in self.selected_layers:
                    try:
                        if old_lay.dxf.name not in new_doc.layers:
                            nl = new_doc.layers.add(name=old_lay.dxf.name)
                            nl.dxf.color = old_lay.dxf.color
                            nl.dxf.linetype = old_lay.dxf.linetype
                    except Exception: pass
            
            success_cnt = 0
            for ent in ents:
                try:
                    # Injects entity + required blocks references safely
                    new_msp.add_foreign_entity(ent, copy=True)
                    success_cnt += 1
                except Exception: pass
                
            new_doc.saveas(save_path)
            messagebox.showinfo("Hoàn tất", f"Đã lưu thành công {success_cnt} phần tử ra file:\n{save_path}")
            self.set_status(f"Đã lưu file DXF: {os.path.basename(save_path)}")
        except Exception as e:
            messagebox.showerror("Lỗi Lưu File", f"Thất bại:\n{e}")

    def export_to_mask(self):
        if not self.doc: return
        
        _, paths = self.get_filtered_dataset()
        if not paths:
            messagebox.showwarning("Trống", "Không có nét hình nào để tạo ảnh!")
            return
            
        try:
            w_target = int(self.var_mask_width.get())
            thick = int(self.var_thickness.get())
            if w_target < 100 or thick < 1: raise ValueError
        except ValueError:
            messagebox.showerror("Lỗi nhập liệu", "Kiểm tra độ rộng & độ dày nhập vào.")
            return
            
        save_path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG Image", "*.png")],
            initialfile="nike_mask.png"
        )
        if not save_path: return
        
        self.set_status("Đang tính toán tọa độ và sinh Mask...")
        
        try:
            all_stacked = np.vstack(paths)
            
            # CORE BUGFIX: Use the exact, absolute bounding box of the EXTRACTED SHAPES
            # instead of the selection drag-box. This guarantees the image perfectly fits the shapes!
            xmin, ymin = np.min(all_stacked, axis=0)
            xmax, ymax = np.max(all_stacked, axis=0)
                
            dx = xmax - xmin
            dy = ymax - ymin
            if dx == 0: dx = 1.0
            if dy == 0: dy = 1.0
            
            pad = int(w_target * 0.03) # 3% buffer margin
            scale = (w_target - 2*pad) / dx
            h_target = int(dy * scale) + 2*pad
            
            self.set_status(f"Đang vẽ mặt nạ kích cỡ {w_target}x{h_target}...")
            self.root.update()
            
            # Black canvas (L mode = Gray)
            img = Image.new("L", (w_target, h_target), 0)
            draw = ImageDraw.Draw(img)
            
            mode = self.var_mask_mode.get()
            
            # Mapping function: CAD Coord -> Image Pixel Coord
            y_max_coord = ymax
            
            def convert_to_px(coord_arr):
                px = pad + (coord_arr[:, 0] - xmin) * scale
                py = pad + (y_max_coord - coord_arr[:, 1]) * scale # Flip Y
                return list(zip(px, py))
            
            for raw_pts in paths:
                pixel_pts = convert_to_px(raw_pts)
                if len(pixel_pts) < 2: continue
                
                if mode == "filled":
                    draw.polygon(pixel_pts, fill=255)
                else:
                    draw.line(pixel_pts, fill=255, width=thick)
                    
            img.save(save_path)
            messagebox.showinfo("Xong", f"Đã vẽ & xuất ảnh thành công!\nLưu tại: {save_path}")
            self.set_status(f"Đã hoàn tất xuất Mask: {os.path.basename(save_path)}")
        except Exception as e:
            messagebox.showerror("Lỗi vẽ ảnh", f"Lỗi: {e}")


if __name__ == "__main__":
    # DPI fix for crisp UI scaling on modern monitors
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception: pass
    
    root = tk.Tk()
    app = DXFProcessorApp(root)
    root.mainloop()
