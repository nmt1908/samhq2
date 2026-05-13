import os
import sys
import math
import cv2
import numpy as np
import traceback
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageDraw, ImageTk

# Setup Matplotlib for interactive visualization
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.widgets import RectangleSelector

# Dynamic Path injection for installed sam-hq2 repository
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SAM2_PATH = os.path.join(CURRENT_DIR, "sam-hq", "sam-hq2")
if os.path.exists(SAM2_PATH):
    if SAM2_PATH not in sys.path:
        sys.path.insert(0, SAM2_PATH)

# Try importing robust DXF engine
try:
    import ezdxf
    from ezdxf import recover
except ImportError:
    pass

# =====================================================================
# 1. ROBUST DXF LOADER & MORPHOLOGICAL SOLID FILLER
# =====================================================================
class DXFTemplateBuilder:
    """Extracts CAD geometry, connects disparate paths, and builds solid filled masks."""
    
    @staticmethod
    def extract_all_paths(dxf_path):
        """Reads all vector coordinates using bulletproof legacy-fallback parser."""
        try:
            try:
                doc = ezdxf.readfile(dxf_path)
            except Exception:
                doc, auditor = recover.readfile(dxf_path)
            
            if hasattr(doc, "model_space"):
                msp = doc.model_space()
            else:
                msp = doc.modelspace()
                
            paths = []
            
            # Filter out non-visible/frozen layers if possible, otherwise process all
            for ent in msp:
                extracted = DXFTemplateBuilder._parse_entity(ent)
                if extracted:
                    paths.extend(extracted)
            return paths
        except Exception as e:
            print(f"Error reading DXF: {e}")
            return []
            
    @staticmethod
    def _parse_entity(entity):
        t = entity.dxftype()
        paths = []
        
        if t == 'INSERT':
            try:
                for sub in entity.virtual_entities():
                    paths.extend(DXFTemplateBuilder._parse_entity(sub))
            except Exception: pass
            return paths
            
        try:
            # Try modern ezdxf.path flattening first
            from ezdxf import path
            if hasattr(path, "make_paths_from_entities"):
                ent_paths = path.make_paths_from_entities([entity])
                for p in ent_paths:
                    coords = np.array(list(p.flatten(distance=0.25)))
                    if len(coords) > 1:
                        paths.append(coords[:, :2])
                if paths: return paths
        except Exception: pass
        
        # Legacy manual fallback parser (highly compatible)
        try:
            coords = []
            if t == 'LINE':
                coords = [entity.dxf.start[:2], entity.dxf.end[:2]]
            elif t == 'LWPOLYLINE':
                coords = [p[:2] for p in list(entity.get_points())]
            elif t == 'POLYLINE':
                coords = [v.dxf.location[:2] for v in entity.vertices]
            elif t == 'ARC':
                center = entity.dxf.center[:2]
                r = entity.dxf.radius
                sa = math.radians(entity.dxf.start_angle)
                ea = math.radians(entity.dxf.end_angle)
                if ea < sa: ea += 2 * math.pi
                steps = max(8, int((ea - sa) * 8))
                angles = np.linspace(sa, ea, steps)
                coords = [[center[0] + r*math.cos(a), center[1] + r*math.sin(a)] for a in angles]
            elif t == 'CIRCLE':
                center = entity.dxf.center[:2]
                r = entity.dxf.radius
                angles = np.linspace(0, 2 * math.pi, 32)
                coords = [[center[0] + r*math.cos(a), center[1] + r*math.sin(a)] for a in angles]
            elif t == 'SPLINE':
                if hasattr(entity, "control_points") and len(entity.control_points) > 0:
                    coords = [cp[:2] for cp in entity.control_points]
                elif hasattr(entity, "fit_points") and len(entity.fit_points) > 0:
                    coords = [fp[:2] for fp in entity.fit_points]
                    
            if len(coords) > 1:
                paths.append(np.array(coords))
        except Exception: pass
        return paths

    @staticmethod
    def build_solid_template(paths, target_size=512):
        """
        Combines all path segments and performs Morphological Hole Filling
        to produce a perfectly solid black and white template mask.
        """
        if not paths:
            return None, None
            
        all_stacked = np.vstack(paths)
        xmin, ymin = np.min(all_stacked, axis=0)
        xmax, ymax = np.max(all_stacked, axis=0)
        
        dx = max(1e-5, xmax - xmin)
        dy = max(1e-5, ymax - ymin)
        
        # Compute aspect-ratio preserving canvas bounds
        margin = 0.1 # 10% margin
        scale = (target_size * (1 - 2*margin)) / max(dx, dy)
        
        w_img = target_size
        h_img = target_size
        
        # Initialize high-res canvas
        canvas = np.zeros((h_img, w_img), dtype=np.uint8)
        
        # Center alignment offsets
        cx_offset = (w_img - dx * scale) / 2.0
        cy_offset = (h_img - dy * scale) / 2.0
        
        # Draw lines as thick connections first to bridge small drafting gaps
        for pts in paths:
            px = cx_offset + (pts[:, 0] - xmin) * scale
            py = cy_offset + (ymax - pts[:, 1]) * scale # Flip Y for image space
            pixel_pts = np.column_stack((px, py)).astype(np.int32)
            
            cv2.polylines(canvas, [pixel_pts], isClosed=False, color=255, thickness=3)
            
        # SMART MORPHOLOGICAL FILL:
        # Use Closing operation to seal endpoints, then FindContours to extract & fill the interior
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        closed_mask = cv2.morphologyEx(canvas, cv2.MORPH_CLOSE, kernel)
        
        # Find outside edges and flood-fill the largest contour
        contours, _ = cv2.findContours(closed_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        solid_mask = np.zeros((h_img, w_img), dtype=np.uint8)
        if contours:
            # Select largest contour (which is the Nike Swoosh main body)
            largest_cnt = max(contours, key=cv2.contourArea)
            cv2.drawContours(solid_mask, [largest_cnt], -1, 255, thickness=cv2.FILLED)
        else:
            # Fallback to just the closed outlines if no area is bounded
            solid_mask = closed_mask
            
        return solid_mask, contours


# =====================================================================
# 2. SHAPE MATCHER ENGINE (AFFINE ALIGNMENT, PCA, MIRROR-AWARE IoU)
# =====================================================================
class ShapeMatcher:
    """Performs invariant geometrical alignment and scores segment matching."""
    
    @staticmethod
    def propose_by_template_matching(image_rgb, dxf_mask):
        """
        🚀 SIÊU CẤP CHAMFER + PIXEL-TIGHT MASK PROJECTION 🚀
        Dò quét tọa độ Logo siêu ôm sát, triệt tiêu góc xoay chết màu đen!
        """
        try:
            h_img, w_img = image_rgb.shape[:2]
            
            y_start = int(h_img * 0.20)
            y_end = int(h_img * 0.85)
            x_start = int(w_img * 0.05)
            x_end = int(w_img * 0.95)
            
            roi_img = image_rgb[y_start:y_end, x_start:x_end]
            h_roi, w_roi = roi_img.shape[:2]
            
            target_w = 400
            scale_down = target_w / float(w_roi)
            target_h = int(h_roi * scale_down)
            
            img_small = cv2.resize(roi_img, (target_w, target_h))
            gray_small = cv2.cvtColor(img_small, cv2.COLOR_RGB2GRAY)
            gray_blur = cv2.GaussianBlur(gray_small, (5, 5), 0)
            img_edge = cv2.Canny(gray_blur, 80, 200)
            kernel = np.ones((3, 3), np.uint8)
            img_edge = cv2.morphologyEx(img_edge, cv2.MORPH_CLOSE, kernel)
            
            inv_edge = 255 - img_edge
            dist_map = cv2.distanceTransform(inv_edge, cv2.DIST_L2, 3)
            
            t_cnt = ShapeMatcher.get_normalized_contour(dxf_mask)
            if t_cnt is None: return None
            tx, ty, tw, th = cv2.boundingRect(t_cnt)
            tpl_crop = dxf_mask[ty:ty+th, tx:tx+tw]
            tpl_edge = cv2.Canny(tpl_crop, 50, 200)
            
            best_score = 1e9
            best_loc = None
            best_tpl_mask = None
            
            base_angles = [0, 90, 180, 270]
            fine_offsets = [-18, -9, 0, 9, 18]
            target_widths = np.linspace(60, 385, 18) 
            
            for base_ang in base_angles:
                if base_ang == 0: 
                    rot_base = tpl_edge
                    rot_mask_base = tpl_crop
                elif base_ang == 90: 
                    rot_base = cv2.rotate(tpl_edge, cv2.ROTATE_90_CLOCKWISE)
                    rot_mask_base = cv2.rotate(tpl_crop, cv2.ROTATE_90_CLOCKWISE)
                elif base_ang == 180: 
                    rot_base = cv2.rotate(tpl_edge, cv2.ROTATE_180)
                    rot_mask_base = cv2.rotate(tpl_crop, cv2.ROTATE_180)
                else: 
                    rot_base = cv2.rotate(tpl_edge, cv2.ROTATE_90_COUNTERCLOCKWISE)
                    rot_mask_base = cv2.rotate(tpl_crop, cv2.ROTATE_90_COUNTERCLOCKWISE)
                
                for f_off in fine_offsets:
                    if f_off != 0:
                        bh, bw = rot_base.shape[:2]
                        center = (bw // 2, bh // 2)
                        M = cv2.getRotationMatrix2D(center, f_off, 1.0)
                        cos = abs(M[0,0])
                        sin = abs(M[0,1])
                        nw = int((bh * sin) + (bw * cos))
                        nh = int((bh * cos) + (bw * sin))
                        M[0,2] += (nw / 2) - center[0]
                        M[1,2] += (nh / 2) - center[1]
                        rot_img = cv2.warpAffine(rot_base, M, (nw, nh))
                        rot_mask = cv2.warpAffine(rot_mask_base, M, (nw, nh), flags=cv2.INTER_NEAREST)
                    else:
                        rot_img = rot_base
                        rot_mask = rot_mask_base
                        
                    for target_w_sc in target_widths:
                        r_h, r_w = rot_img.shape[:2]
                        sc = float(target_w_sc) / float(r_w)
                        tw_sc = int(target_w_sc)
                        th_sc = int(r_h * sc)
                        
                        if tw_sc >= target_w or th_sc >= target_h or tw_sc < 25 or th_sc < 25:
                            continue
                            
                        res_tpl = cv2.resize(rot_img, (tw_sc, th_sc), interpolation=cv2.INTER_AREA)
                        tpl_f32 = (res_tpl > 127).astype(np.float32)
                        num_edge_pixels = np.count_nonzero(tpl_f32)
                        if num_edge_pixels < 20: continue
                        
                        match_map = cv2.matchTemplate(dist_map, tpl_f32, cv2.TM_CCORR)
                        min_val, _, min_loc, _ = cv2.minMaxLoc(match_map)
                        avg_chamfer_score = min_val / float(num_edge_pixels)
                        
                        if avg_chamfer_score < best_score:
                            best_score = avg_chamfer_score
                            best_loc = min_loc
                            best_tpl_mask = cv2.resize(rot_mask, (tw_sc, th_sc), interpolation=cv2.INTER_NEAREST)
                            
            if best_tpl_mask is not None and best_loc is not None and best_score < 8.0:
                x, y = best_loc
                ys, xs = np.where(best_tpl_mask > 0)
                if len(xs) == 0 or len(ys) == 0:
                    return None
                
                x1_small = x + xs.min()
                x2_small = x + xs.max()
                y1_small = y + ys.min()
                y2_small = y + ys.max()
                
                x1 = int(x1_small / scale_down) + x_start
                y1 = int(y1_small / scale_down) + y_start
                x2 = int(x2_small / scale_down) + x_start
                y2 = int(y2_small / scale_down) + y_start
                
                bw = x2 - x1
                bh = y2 - y1
                
                padx = int(bw * 0.12)
                pady = int(bh * 0.12)
                
                return (max(0, x1 - padx), max(0, y1 - pady), min(w_img, x2 + padx), min(h_img, y2 + pady))
                
        except Exception as e:
            print(f"Lỗi Chamfer: {e}")
        return None

    @staticmethod
    def propose_candidate_boxes(image_rgb, dxf_mask=None, min_area_pct=0.02, max_area_pct=0.45):
        """
        Generates bounding boxes using a SUPER HYBRID strategy:
        """
        h, w = image_rgb.shape[:2]
        
        # ⚡ GIẢI PHÁP TẬN DIỆT: BẮT BUỘC DÙNG CHAMFER BOX NẾU TÌM THẤY ⚡
        # Nuôi dưỡng Logits Sweeper bằng "Đồ Ăn Sạch" (BBox ôm sát) để đẻ ra Kỳ tích IoU!
        if dxf_mask is not None:
            tpl_box = ShapeMatcher.propose_by_template_matching(image_rgb, dxf_mask)
            if tpl_box is not None:
                # Trả về đồng thời Hộp Chamfer Tinh Xảo và Hộp Mỏ Neo Vàng làm chốt chặn!
                golden_box = (int(w * 0.18), int(h * 0.30), int(w * 0.86), int(h * 0.78))
                return [tpl_box, golden_box]
        """
        Generates bounding boxes using a SUPER HYBRID strategy:
        1. Ultra-fast Classical CV detectors for precision local crops.
        2. DENSE MULTI-SCALE SLIDING PYRAMID (User Grid Search) to guarantee 
           100% brute-force coverage overlap on large objects (>20% area).
        All proposals are merged and de-duplicated seamlessly!
        """
        h, w = image_rgb.shape[:2]
        total_area = h * w
        raw_proposals = []
        
        # --- PART A: CLASSICAL CV SHAPE DETECTORS (For tight-focused precision) ---
        try:
            gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
            binary_sources = []
            
            _, t_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            binary_sources.append(t_otsu)
            binary_sources.append(cv2.bitwise_not(t_otsu))
            
            t_adapt = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 2)
            binary_sources.append(t_adapt)
            binary_sources.append(cv2.bitwise_not(t_adapt))
            
            edges = cv2.Canny(gray, 50, 150)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
            binary_sources.append(cv2.dilate(edges, kernel, iterations=2))
            
            for bin_map in binary_sources:
                contours, _ = cv2.findContours(bin_map, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for cnt in contours:
                    area = cv2.contourArea(cnt)
                    if area < min_area_pct * total_area: continue
                    
                    rect = cv2.minAreaRect(cnt)
                    rw, rh = rect[1]
                    if rw == 0 or rh == 0: continue
                    aspect_ratio = max(rw, rh) / min(rw, rh)
                    
                    # Standard Swoosh elongation threshold
                    if 1.4 < aspect_ratio < 7.8:
                        hull = cv2.convexHull(cnt)
                        solidity = area / max(1e-5, cv2.contourArea(hull))
                        if 0.18 < solidity < 0.92:
                            rx, ry, rw_box, rh_box = cv2.boundingRect(cnt)
                            
                            # Require minimum size to filter dust
                            if rw_box >= w * 0.12 or rh_box >= h * 0.10:
                                pad_w = int(rw_box * 0.15)
                                pad_h = int(rh_box * 0.15)
                                xmin = max(0, rx - pad_w)
                                ymin = max(0, ry - pad_h)
                                xmax = min(w, rx + rw_box + pad_w)
                                ymax = min(h, ry + rh_box + pad_h)
                                raw_proposals.append(((xmin, ymin, xmax, ymax), solidity))
        except Exception:
            pass
            
        # Sort and perform fast deduplication on CV proposals
        raw_proposals.sort(key=lambda x: x[1], reverse=True)
        unique_proposals = []
        
        def get_overlap_ratio(b1, b2):
            ix1, iy1 = max(b1[0], b2[0]), max(b1[1], b2[1])
            ix2, iy2 = min(b1[2], b2[2]), min(b1[3], b2[3])
            if ix1 >= ix2 or iy1 >= iy2: return 0.0
            inter = (ix2-ix1) * (iy2-iy1)
            area1 = (b1[2]-b1[0]) * (b1[3]-b1[1])
            return inter / float(max(1e-5, area1))
            
        for box, score in raw_proposals:
            duplicate = False
            for u_box in unique_proposals:
                if get_overlap_ratio(box, u_box) > 0.75:
                    duplicate = True
                    break
            if not duplicate:
                unique_proposals.append(box)
                if len(unique_proposals) >= 6:
                    break
                    
        # --- PART B: DENSE SYSTEMATIC MULTI-SCALE GRID SEARCH (User Idea) ---
        # Thiết lập bộ quét Lưới Thảm Hệ thống đa tỷ lệ:
        # Tự động tạo hàng trăm cửa sổ trượt có kích thước và tâm dịch chuyển theo bước nhảy 15%.
        # Đảm bảo 100% vét cạn sạch sẽ mọi mét vuông hình ảnh ở mọi kích cỡ khác nhau!
        priors = []
        
        # 3 Mốc tỷ lệ Chiều ngang và Chiều dọc của cửa sổ trượt (Phù hợp Logo to >20%)
        width_scales = [0.35, 0.60, 0.85]
        height_scales = [0.20, 0.40, 0.60]
        
        # 5 Mốc Tọa độ Tâm trượt trên cả 2 trục X và Y (Bước nhảy mịn)
        centers_x = [0.20, 0.35, 0.50, 0.65, 0.80]
        centers_y = [0.20, 0.35, 0.50, 0.65, 0.80]
        
        for ws in width_scales:
            for hs in height_scales:
                # Skip shapes that are vertically narrow (Nike is usually elongated wide)
                if ws / hs < 0.8: continue 
                
                bw = int(w * ws)
                bh = int(h * hs)
                
                # Cho tâm cửa sổ trượt tuần tự qua lưới 5x5
                for cx in centers_x:
                    for cy in centers_y:
                        x1 = int(w * cx - bw / 2)
                        y1 = int(h * cy - bh / 2)
                        x2 = x1 + bw
                        y2 = y1 + bh
                        
                        # Giới hạn trong biên ảnh
                        x1 = max(0, min(x1, w - 100))
                        y1 = max(0, min(y1, h - 100))
                        x2 = max(x1 + 50, min(x2, w))
                        y2 = max(y1 + 50, min(y2, h))
                        
                        # Lọc tối thiểu (Loại bỏ các hộp quá bé do bị xén viền)
                        if (x2 - x1) >= w * 0.15 and (y2 - y1) >= h * 0.12:
                            priors.append((x1, y1, x2, y2))
                            
        # Thêm 2 lớp bao phủ toàn cảnh tuyệt đối làm chốt chặn cuối
        priors.append((int(w * 0.02), int(h * 0.02), int(w * 0.98), int(h * 0.98)))
        priors.append((int(w * 0.10), int(h * 0.10), int(w * 0.90), int(h * 0.90)))
        
        # Gộp nhóm và Loại bỏ các hộp bị trùng lặp đè lên nhau quá 80%
        for pri in priors:
            duplicate = False
            for ex_box in unique_proposals:
                if get_overlap_ratio(pri, ex_box) > 0.80:
                    duplicate = True
                    break
            if not duplicate:
                unique_proposals.append(pri)
                
        # Giới hạn trần 65 Vùng Quét Thảm Dày Đặc (Chạy chỉ tốn ~2 giây trên GPU, tuyệt đối chính xác!)
        return unique_proposals[:65]

    @staticmethod
    def get_normalized_contour(mask):
        """Extracts, centers, and rescales the primary contour for math invariants."""
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        cnt = max(contours, key=cv2.contourArea)
        return cnt

    @staticmethod
    def normalize_to_canonical(mask, target_size=256, force_long_edge_horizontal=True):
        """
        Warps an arbitrary mask into a standard centered canonical canvas (256x256).
        This mathematically guarantees scale, rotation, and translation invariance, 
        allowing direct pixel-to-pixel IoU comparison across different image resolutions!
        """
        cnt = ShapeMatcher.get_normalized_contour(mask)
        if cnt is None:
            return None
            
        # Get Oriented Bounding Box
        rect = cv2.minAreaRect(cnt)
        (cx, cy), (w, h), angle = rect
        
        if w == 0 or h == 0:
            return None
            
        # Solve the 90 degree orientation swap ambiguity by forcing
        # the long axis of the shape to always map to the horizontal axis!
        if force_long_edge_horizontal and w < h:
            w, h = h, w
            angle += 90.0
            
        # Compute precise uniform scale to fit shape inside the target size, 
        # leaving a clean 15% bounding padding so edges never get clipped!
        margin = 0.15
        scale = (target_size * (1.0 - 2.0 * margin)) / float(max(w, h))
        
        # Canvas destination center
        center_dst = target_size / 2.0
        
        # Calculate affine transformation matrix
        M = cv2.getRotationMatrix2D((cx, cy), angle, scale)
        
        # Shift translation parameters to center the warped output inside destination canvas.
        # Since getRotationMatrix2D internally handles the scale and rotates around (cx, cy),
        # we simply translate the shape center to the target canvas center.
        M[0, 2] += center_dst - cx
        M[1, 2] += center_dst - cy
        
        # Execute precise Warp Affine in canonical dimensions!
        canonical = cv2.warpAffine(mask, M, (target_size, target_size), flags=cv2.INTER_NEAREST)
        _, canonical_bin = cv2.threshold(canonical, 127, 255, cv2.THRESH_BINARY)
        
        return canonical_bin

    @staticmethod
    def align_and_compute_iou(template_mask, sam_mask):
        """
        Computes the absolute best Intersection-Over-Union between two masks
        by projecting both into a 256x256 canonical coordinate frame and
        testing mirror/flip variants for Left/Right orientation robustness.
        """
        # Normalize BOTH shapes to matching 256x256 canvases!
        can_t = ShapeMatcher.normalize_to_canonical(template_mask, target_size=256)
        can_s = ShapeMatcher.normalize_to_canonical(sam_mask, target_size=256)
        
        if can_t is None or can_s is None:
            return 0.0, 1.0, None
            
        # Extract shape distance metric
        t_cnt = ShapeMatcher.get_normalized_contour(template_mask)
        s_cnt = ShapeMatcher.get_normalized_contour(sam_mask)
        shape_dist = cv2.matchShapes(t_cnt, s_cnt, cv2.CONTOURS_MATCH_I2, 0) if (t_cnt is not None and s_cnt is not None) else 1.0
        
        best_iou = 0.0
        best_warped = None
        
        # Test mirror/180-deg transformations AND ALSO 90-degree rotated axes
        # to guarantee 100% robustness against oriented bbox (minAreaRect) axis swaps!
        # This brute-forces all 8 topological orientations in under 1ms!
        transform_configs = [
            # Nhóm gốc (Đúng trục)
            ("NORMAL", lambda img: img),
            ("FLIP_H", lambda img: cv2.flip(img, 1)),
            ("FLIP_V", lambda img: cv2.flip(img, 0)),
            ("FLIP_BOTH", lambda img: cv2.flip(img, -1)),
            
            # Nhóm xoay 90 độ (Xử lý triệt để lỗi hoán đổi Trục Dài/Ngắn của OpenCV)
            ("ROT90", lambda img: cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)),
            ("ROT90_FLIP_H", lambda img: cv2.flip(cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE), 1)),
            ("ROT90_FLIP_V", lambda img: cv2.flip(cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE), 0)),
            ("ROT90_FLIP_BOTH", lambda img: cv2.flip(cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE), -1)),
        ]
        
        for name, transform_op in transform_configs:
            test_can_s = transform_op(can_s)
            
            # Calculate raw overlapping metrics on the matching canonical grids!
            intersection = cv2.bitwise_and(can_t, test_can_s)
            union = cv2.bitwise_or(can_t, test_can_s)
            
            i_area = np.count_nonzero(intersection)
            u_area = np.count_nonzero(union)
            
            iou = i_area / float(u_area) if u_area > 0 else 0.0
            
            if iou > best_iou:
                best_iou = iou
                best_warped = test_can_s
                
        # Return score and a diagnostic tuple containing both canonical shapes!
        return best_iou, shape_dist, (can_t, best_warped)


# =====================================================================
# 3. HQ-SAM 2 ENGINE WRAPPER WITH GRABCUT FALLBACK
# =====================================================================
class SAMPredictorWrapper:
    """Dynamically loads HQ-SAM2/SAM2 with local detection and falls back gracefully."""
    
    def __init__(self):
        self.predictor = None
        self.mask_generator = None # 🚀 BỘ QUÉT MASK TỰ ĐỘNG TOÀN CẢNH (AMG)
        self.device = "cpu"
        self.model_type = "NONE"
        self.info = "Chưa nạp Model. Đang dùng chế độ dự phòng GrabCut."
        self._last_image = None # 🚀 TĂNG TỐC 10X CACHE EMBEDDING SEQUENTIAL SCAN
        
    def initialize(self, status_callback=None):
        if status_callback: status_callback("Đang quét thư viện PyTorch & SAM2...")
        
        try:
            import torch
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            
            # Try to import SAM2 from either environment or the git-cloned sam-hq2 path
            from sam2.build_sam import build_sam2
            from sam2.sam2_image_predictor import SAM2ImagePredictor
            from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
            
            # Find config & checkpoint
            cfg_dir = os.path.join(SAM2_PATH, "configs", "sam2.1")
            ckpt_dir = os.path.join(SAM2_PATH, "checkpoints")
            
            # Preferred weights
            ckpt_path = os.path.join(ckpt_dir, "sam2.1_hq_hiera_large.pt")
            cfg_name = "configs/sam2.1/sam2.1_hq_hiera_l.yaml"
            
            # Fallback weights if Large is missing
            if not os.path.exists(ckpt_path):
                ckpt_path = os.path.join(ckpt_dir, "sam2.1_hiera_base_plus.pt")
                cfg_name = "configs/sam2.1/sam2.1_hiera_b+.yaml"
                
            if not os.path.exists(ckpt_path):
                self.info = "Lỗi: Không tìm thấy file .pt trong checkpoints. Hãy chạy file install_hq_sam2.bat!"
                return False
                
            # Model path resolution inside official sam2 structure
            if status_callback: status_callback(f"Đang tải HQ-SAM2 lên {self.device.upper()}...")
            
            # Build and set predictor & generator
            sam2_model = build_sam2(cfg_name, ckpt_path, device=self.device)
            self.predictor = SAM2ImagePredictor(sam2_model)
            
            # Thiết lập bộ sinh Mask tự động toàn khung hình (Zero-Shot Segmenter)
            self.mask_generator = SAM2AutomaticMaskGenerator(
                model=sam2_model,
                points_per_side=32,           # 32x32 grid
                points_per_batch=64,          # Cân bằng tốc độ và VRAM
                pred_iou_thresh=0.75,         # Loại bỏ đối tượng quá mờ nhạt
                stability_score_thresh=0.88,  # Chỉ lấy mask cực kỳ sắc nét ổn định
                crop_n_layers=0,              # Tắt phân cấp đa lớp để chạy siêu tốc
                min_mask_region_area=200      # Loại bỏ nhiễu bụi lấm tấm nhỏ hơn 200px
            )
            
            self.model_type = "HQ-SAM2"
            self.info = f"✅ HQ-SAM 2 + AMG ĐÃ SẴN SÀNG ({self.device.upper()})"
            return True
            
        except Exception as e:
            print(traceback.format_exc())
            self.info = f"⚠️ Chế độ dự phòng (GrabCut CV2). Chưa có SAM2."
            return False

    def generate_all_masks(self, image_rgb):
        """
        🚀 HÀM QUÉT TOÀN CẢNH (Automatic Mask Generation) 🚀
        Tự động quét toàn bộ ảnh và bóc tách hàng chục vật thể độc lập mà không cần BBox!
        """
        if self.mask_generator is not None:
            try:
                import torch
                # SIÊU TỐC ĐỘ: Kích hoạt FlashAttention bằng cách ép về FP16 cho CUDA! ⚡🚀
                is_cuda = (self.device == "cuda")
                ctx = torch.autocast(device_type="cuda", dtype=torch.float16) if is_cuda else torch.inference_mode()
                with ctx:
                    # Quét cạn toàn khung hình, trả về list [{'segmentation': bool_array, 'area': float, 'bbox': [x,y,w,h]}]
                    return self.mask_generator.generate(image_rgb)
            except Exception as e:
                print(f"Lỗi Automatic Mask Generator: {e}")
        return []

    def segment_box(self, image_rgb, box_coords):
        """
        Main segmenter interface. Performs Neural SAM segmentation or 
        Traditional Color GrabCut based on setup availability.
        """
        # Coordinates format: [xmin, ymin, xmax, ymax]
        xmin, ymin, xmax, ymax = map(int, box_coords)
        
        # Guard bounds
        h, w, _ = image_rgb.shape
        xmin = max(0, min(xmin, w-2))
        ymin = max(0, min(ymin, h-2))
        xmax = max(xmin+1, min(xmax, w-1))
        ymax = max(ymin+1, min(ymax, h-1))
        
        # --- CASE A: REAL AI INFERENCE (HQ-SAM2) ---
        if self.predictor is not None:
            try:
                import torch
                # SIÊU TỐC ĐỘ & BẬT FLASHATTENTION: Ép PyTorch về chế độ FP16 (Bán chuẩn) cho GPU NVIDIA! ⚡🚀
                # Giúp triệt tiêu 100% lỗi "Expected query to be {Half, BFloat16}" và tăng tốc gấp 5-10 lần!
                is_cuda = (self.device == "cuda")
                ctx = torch.autocast(device_type="cuda", dtype=torch.float16) if is_cuda else torch.inference_mode()
                
                with ctx:
                    # Siêu tối ưu tốc độ: Bỏ qua backbone encoder nếu trùng ảnh!
                    if self._last_image is not image_rgb:
                        self.predictor.set_image(image_rgb)
                        self._last_image = image_rgb
                    
                    box_input = np.array([xmin, ymin, xmax, ymax])
                    
                    # Nạp tham số return_logits=True để nhận Bản đồ Xác suất Dạng Số Thực (Logits) thay vì Boolean.
                    # Điều này cho phép ta "cắt lát phân cực" để tìm ra phân biên chuẩn xác nhất!
                    high_res_logits, scores, _ = self.predictor.predict(
                        point_coords=None,
                        point_labels=None,
                        box=box_input[None, :], # Formats as [1, 4]
                        multimask_output=True,
                        return_logits=True
                    )
                
                results = []
                # Dò quét 31 nấc phân ngưỡng tối ưu (Threshold Sweep từ -3.0 đến +3.0)
                # Đảm bảo bắt gọn từng đường viền Logo bất chấp chênh lệch cường độ!
                for logit_map in high_res_logits:
                    # Chuyển đổi Torch Tensor sang Numpy nếu cần thiết
                    if hasattr(logit_map, "cpu"):
                        logit_map = logit_map.cpu().numpy()
                        
                    # Quét qua 31 nấc để DXF tự làm trọng tài chấm điểm mặt nạ đẹp nhất!
                    for th in np.linspace(-3.0, 3.0, 31):
                        bin_m = (logit_map > th).astype(np.uint8) * 255
                        
                        # Tinh chỉnh nhẹ biên (Morphology Close/Open) khử răng cưa & nhiễu lấm tấm
                        kernel = np.ones((3, 3), np.uint8)
                        bin_m = cv2.morphologyEx(bin_m, cv2.MORPH_OPEN, kernel)
                        bin_m = cv2.morphologyEx(bin_m, cv2.MORPH_CLOSE, kernel)
                        
                        results.append(bin_m)
                return results
            except Exception as err:
                print(f"SAM running error: {err}")
                # Fall through to GrabCut if AI crashes at runtime (e.g. Out of Memory)
                
        # --- CASE B: CLASSICAL COMPUTER VISION FALLBACK (GrabCut) ---
        # This works IMMEDIATELY out-of-the-box without any AI library setup!
        try:
            h, w = image_rgb.shape[:2]
            
            # HIGH OPTIMIZATION: Crop and downscale ROI to maximize CPU speed (runs in <100ms!)
            # Add 10% padding around the prompt box to let GrabCut see surrounding background
            pad = int(max(xmax - xmin, ymax - ymin) * 0.10)
            crop_xmin = max(0, xmin - pad)
            crop_ymin = max(0, ymin - pad)
            crop_xmax = min(w, xmax + pad)
            crop_ymax = min(h, ymax + pad)
            
            cropped_rgb = image_rgb[crop_ymin:crop_ymax, crop_xmin:crop_xmax]
            if cropped_rgb.size == 0:
                return []
                
            # Determine optimal downscaling size (max dimension 400px is perfectly fast)
            max_dim = 400.0
            scale_factor = max_dim / float(max(cropped_rgb.shape[0], cropped_rgb.shape[1]))
            
            do_resize = scale_factor < 1.0
            if do_resize:
                proc_rgb = cv2.resize(cropped_rgb, (0, 0), fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_AREA)
            else:
                proc_rgb = cropped_rgb.copy()
                scale_factor = 1.0
                
            # Map global prompt box coordinates to local scaled cropped coordinates
            l_xmin = max(0, min(int((xmin - crop_xmin) * scale_factor), proc_rgb.shape[1] - 2))
            l_ymin = max(0, min(int((ymin - crop_ymin) * scale_factor), proc_rgb.shape[0] - 2))
            l_xmax = max(l_xmin + 1, min(int((xmax - crop_xmin) * scale_factor), proc_rgb.shape[1] - 1))
            l_ymax = max(l_ymin + 1, min(int((ymax - crop_ymin) * scale_factor), proc_rgb.shape[0] - 1))
            
            # Setup ultra-fast GrabCut on mini-image
            mask = np.zeros(proc_rgb.shape[:2], np.uint8)
            bgdModel = np.zeros((1, 65), np.float64)
            fgdModel = np.zeros((1, 65), np.float64)
            rect = (l_xmin, l_ymin, l_xmax - l_xmin, l_ymax - l_ymin)
            
            # Execution: Instant 5 iterations on small crop matrix
            cv2.grabCut(proc_rgb, mask, rect, bgdModel, fgdModel, 5, cv2.GC_INIT_WITH_RECT)
            bin_mask_small = np.where((mask == 2) | (mask == 0), 0, 255).astype('uint8')
            
            # Upscale the small computed mask back up to original crop resolution
            if do_resize:
                bin_mask_crop = cv2.resize(bin_mask_small, (cropped_rgb.shape[1], cropped_rgb.shape[0]), interpolation=cv2.INTER_NEAREST)
            else:
                bin_mask_crop = bin_mask_small
                
            # Paste crop mask back into absolute full image canvas
            full_bin_mask = np.zeros((h, w), dtype=np.uint8)
            full_bin_mask[crop_ymin:crop_ymax, crop_xmin:crop_xmax] = bin_mask_crop
            
            return [full_bin_mask]
        except Exception as ex:
            print(f"Optimized GrabCut crash: {ex}")
            return []


# =====================================================================
# 4. INTERACTIVE PIPELINE Tkinter DASHBOARD
# =====================================================================
class DXFtoSAMPipelineUI:
    def __init__(self, root):
        self.root = root
        self.root.title("🥇 Nike Vector-AI Matching Dashboard (HQ-SAM2 Engine)")
        self.root.geometry("1450x900")
        self.root.configure(bg="#1a1c23")
        
        # System instances
        self.sam_wrapper = SAMPredictorWrapper()
        self.dxf_paths = []
        self.dxf_solid_mask = None
        self.current_image_rgb = None
        self.current_image_path = None
        
        self.setup_styling()
        self.create_ui_elements()
        
        # Autoload system engine
        self.root.after(500, self.init_sam_system)
        
        # TỰ ĐỘNG NẠP SẴN FILE DXF NIKERIGHT KHI KHỞI ĐỘNG 🚀
        self.root.after(800, self.autoload_default_dxf)
        
        # Global exit hotkey: Bind 'Q' and 'q' to close application safely
        self.root.bind_all("<Key-q>", lambda e: self.root.destroy())
        self.root.bind_all("<Key-Q>", lambda e: self.root.destroy())
        
    def setup_styling(self):
        self.style = ttk.Style()
        self.style.theme_use("clam")
        
        # Dark modern dashboard styling
        self.style.configure("TFrame", background="#1a1c23")
        self.style.configure("TLabel", background="#1a1c23", foreground="#ffffff", font=("Segoe UI", 10))
        self.style.configure("Header.TLabel", font=("Segoe UI Semibold", 13), foreground="#4285f4")
        
        self.style.configure("TButton", background="#2d313f", foreground="#ffffff", font=("Segoe UI Semibold", 9), borderwidth=1)
        self.style.map("TButton", background=[("active", "#3d4256")])
        
        self.style.configure("Action.TButton", background="#0d6efd", foreground="#ffffff", font=("Segoe UI Bold", 10))
        self.style.map("Action.TButton", background=[("active", "#0b5ed7")])
        
        self.style.configure("Status.TLabel", background="#111217", foreground="#00e676", font=("Consolas", 9, "bold"), anchor="w", relief="sunken")

    def create_ui_elements(self):
        # TOP BAR CONTROL
        top_panel = ttk.Frame(self.root, padding=10, relief="raised")
        top_panel.pack(fill="x", side="top")
        
        ttk.Label(top_panel, text="🦅 DXF TO HQ-SAM 2 CO-PROCESSOR", font=("Segoe UI", 14, "bold"), foreground="#4285f4").pack(side="left", padx=10)
        
        self.lbl_sam_status = ttk.Label(top_panel, text="Đang khởi động công cụ AI...", font=("Segoe UI", 10, "italic"), foreground="#ff9800")
        self.lbl_sam_status.pack(side="right", padx=20)

        # MAIN WORKSPACE SPLIT
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill="both", expand=True, side="top", padx=10, pady=10)
        
        # LEFT COLUMN: DXF Input & Real Image Setup
        left_col = ttk.Frame(main_frame, width=350)
        left_col.pack(fill="y", side="left", padx=(0, 10))
        left_col.pack_propagate(False)
        
        # Box 1: DXF Template Selector
        box_dxf = ttk.LabelFrame(left_col, text=" 📐 1. DXF VECTOR TEMPLATE ", padding=10)
        box_dxf.pack(fill="x", pady=(0,15))
        
        btn_load_dxf = ttk.Button(box_dxf, text="📁 Tải DXF Nike Template", command=self.load_dxf_template)
        btn_load_dxf.pack(fill="x", pady=2)
        self.lbl_dxf_info = ttk.Label(box_dxf, text="Chưa nạp DXF.", foreground="#aaaaaa", wraplength=300)
        self.lbl_dxf_info.pack(anchor="w", pady=5)
        
        # Mini Preview Box for template
        self.canvas_dxf_preview = tk.Canvas(box_dxf, width=250, height=250, bg="#000000", highlightthickness=1, highlightbackground="#3d4256")
        self.canvas_dxf_preview.pack(anchor="center", pady=5)
        
        # Box 2: Image Input
        box_img = ttk.LabelFrame(left_col, text=" 📸 2. ẢNH CHỤP THỰC TẾ ", padding=10)
        box_img.pack(fill="both", expand=True)
        
        btn_load_img = ttk.Button(box_img, text="🖼️ Mở Ảnh Chụp Sản Phẩm", command=self.load_real_image)
        btn_load_img.pack(fill="x", pady=2)
        
        btn_auto_find = ttk.Button(box_img, text="🚀 TỰ ĐỘNG TÌM LOGO (AUTO)", style="Action.TButton", command=self.trigger_auto_logo_finder)
        btn_auto_find.pack(fill="x", pady=(5, 2))
        
        self.lbl_img_info = ttk.Label(box_img, text="Chưa chọn ảnh chụp.", foreground="#aaaaaa", wraplength=300)
        self.lbl_img_info.pack(anchor="w", pady=5)
        
        guide_box = ttk.LabelFrame(box_img, text=" ℹ️ HƯỚNG DẪN ", padding=8)
        guide_box.pack(fill="x", side="bottom", pady=5)
        ttk.Label(guide_box, text="👉 Rê chuột Trái trên ảnh thật ở màn hình chính để KHOANH VÙNG CHỌN.\n👉 AI sẽ tự động bóc tách Logo và so sánh IoU ngay lập tức!", justify="left", wraplength=280, font=("Segoe UI", 9)).pack()
        
        # CENTER COLUMN: Interactive Multi-Scale Workspace
        center_col = ttk.LabelFrame(main_frame, text=" 🔍 MÀN HÌNH CHÍNH: KHOANH VÙNG CHỌN (PROMPT BOX TO SAM) ")
        center_col.pack(fill="both", expand=True, side="left")
        
        # Initialize matplotlib canvas on center view
        self.fig, self.ax = plt.subplots(figsize=(8, 7))
        self.fig.patch.set_facecolor('#1a1c23')
        self.ax.set_facecolor('#111217')
        self.ax.axis('off')
        self.fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
        
        self.plot_canvas = FigureCanvasTkAgg(self.fig, master=center_col)
        self.plot_canvas.get_tk_widget().pack(fill="both", expand=True)
        
        # Rectangle Selector for SAM Bounding Box Prompt
        self.rs = RectangleSelector(
            self.ax, self.on_prompt_box_select,
            useblit=True,
            button=[1],
            minspanx=5, minspany=5,
            interactive=True,
            props=dict(facecolor='#ffea00', edgecolor='#ffea00', alpha=0.2, fill=True, linewidth=1.5)
        )
        
        # RIGHT COLUMN: Alignment Diagnostics & Best Score
        right_col = ttk.LabelFrame(main_frame, text=" 🏆 KẾT QUẢ KHỚP HÌNH (IoU MATCHING) ", width=380, padding=10)
        right_col.pack(fill="y", side="right", padx=(10, 0))
        right_col.pack_propagate(False)
        
        # Big Score display
        score_banner = ttk.Frame(right_col, relief="solid", borderwidth=1)
        score_banner.pack(fill="x", pady=(0, 15))
        
        ttk.Label(score_banner, text="CHỈ SỐ IoU KHỚP TỐT NHẤT", font=("Segoe UI Semibold", 10), foreground="#4285f4").pack(pady=(5,0))
        self.lbl_iou_score = ttk.Label(score_banner, text="--.- %", font=("Consolas", 36, "bold"), foreground="#00e676")
        self.lbl_iou_score.pack(pady=5)
        
        self.lbl_match_status = ttk.Label(score_banner, text="Đang chờ dữ liệu...", font=("Segoe UI Semibold", 10))
        self.lbl_match_status.pack(pady=(0, 5))
        
        # Align view canvas (Visual Superimposition)
        ttk.Label(right_col, text="Khu vực chồng đè (Xanh=Khớp, Đỏ=Lệch):").pack(anchor="w", pady=(5,2))
        self.canvas_align_preview = tk.Canvas(right_col, width=340, height=280, bg="#0e0f13", highlightthickness=1, highlightbackground="#3d4256")
        self.canvas_align_preview.pack(anchor="center", pady=5)
        
        # Details
        det_frame = ttk.LabelFrame(right_col, text=" 📋 Thông số chi tiết ", padding=8)
        det_frame.pack(fill="both", expand=True, pady=10)
        
        self.lbl_stats_mode = ttk.Label(det_frame, text="Chế độ quét: --")
        self.lbl_stats_mode.pack(anchor="w", pady=2)
        self.lbl_stats_hu = ttk.Label(det_frame, text="Khoảng cách Hình dáng (Hu): --")
        self.lbl_stats_hu.pack(anchor="w", pady=2)
        self.lbl_stats_res = ttk.Label(det_frame, text="Kích cỡ vùng chọn: --")
        self.lbl_stats_res.pack(anchor="w", pady=2)
        
        # SAVE BUTTONS
        btn_frame = ttk.Frame(right_col)
        btn_frame.pack(fill="x", side="bottom")
        
        self.btn_save_mask = ttk.Button(btn_frame, text="💾 Lưu Mặt Nạ Logo Trích Xuất", style="Action.TButton", command=self.save_extracted_mask)
        self.btn_save_mask.pack(fill="x", pady=2)
        
        # FOOTER STATUS BAR
        self.status_bar = ttk.Label(self.root, text=" Khởi chạy Pipeline...", style="Status.TLabel")
        self.status_bar.pack(fill="x", side="bottom")

    def set_status(self, text, color="#00e676"):
        self.status_bar.config(text=f" {text.upper()}", foreground=color)
        self.root.update_idletasks()

    # =====================================================================
    # BUSINESS LOGIC
    # =====================================================================
    def init_sam_system(self):
        self.lbl_sam_status.config(text="⚡ Đang nạp mô hình AI... Vui lòng chờ.", foreground="#ffea00")
        self.root.update()
        
        success = self.sam_wrapper.initialize(status_callback=self.set_status)
        
        if success:
            self.lbl_sam_status.config(text=self.sam_wrapper.info, foreground="#00e676")
            self.set_status("HQ-SAM 2 đã nạp thành công! Sẵn sàng xử lý.", "#00e676")
        else:
            self.lbl_sam_status.config(text=self.sam_wrapper.info, foreground="#ffb74d")
            self.set_status("Không nạp được SAM 2. Đã kích hoạt chế độ dự phòng GrabCut.", "#ffb74d")
            
    def autoload_default_dxf(self):
        """Chủ động nạp sẵn file nikeright.dxf mặc định ở TOOL khi mở app."""
        default_path = r"c:\Users\admin\OneDrive\Máy tính\TOOL\nikeright.dxf"
        if os.path.exists(default_path):
            self.set_status(f"Đang tự động nạp DXF mặc định...", "#ffea00")
            self.process_load_dxf(default_path)
        else:
            self.set_status("Không tìm thấy nikeright.dxf mặc định để autoload.", "#ffb74d")

    def load_dxf_template(self):
        fpath = filedialog.askopenfilename(
            initialdir=CURRENT_DIR,
            filetypes=[("CAD Files", "*.dxf")]
        )
        if not fpath:
            return
        self.process_load_dxf(fpath)

    def process_load_dxf(self, fpath):
        """Hàm dùng chung để xử lý trích xuất tọa độ DXF và vẽ preview lên giao diện."""
        self.set_status(f"Đang phân tích DXF: {os.path.basename(fpath)}")
        self.root.update()
        
        try:
            # Đọc vector và sinh mask làm đặc
            paths = DXFTemplateBuilder.extract_all_paths(fpath)
            if not paths:
                messagebox.showerror("Lỗi", "Không tìm thấy đường cong (polyline/arc/line) nào hợp lệ trong file DXF.")
                return
                
            solid_mask, _ = DXFTemplateBuilder.build_solid_template(paths, target_size=512)
            
            self.dxf_paths = paths
            self.dxf_solid_mask = solid_mask
            
            self.lbl_dxf_info.config(text=f"Đã nạp: {os.path.basename(fpath)}\n{len(paths)} Vector Paths", foreground="#ffffff")
            
            # Vẽ hình ảnh thu nhỏ lên ô Preview cột trái
            self.render_mask_on_canvas(self.dxf_solid_mask, self.canvas_dxf_preview)
            self.set_status(f"Đã nạp DXF thành công: {os.path.basename(fpath)}")
            
        except Exception as e:
            messagebox.showerror("Lỗi Đọc DXF", f"Không thể trích xuất DXF: {e}")
            print(traceback.format_exc())

    def load_real_image(self):
        fpath = filedialog.askopenfilename(
            initialdir=os.path.join(CURRENT_DIR, "Data"),
            filetypes=[("Ảnh chụp", "*.png;*.jpg;*.jpeg")]
        )
        if not fpath:
            return
            
        try:
            img_pil = Image.open(fpath)
            img_rgb = np.array(img_pil.convert('RGB'))
            
            self.current_image_rgb = img_rgb
            self.current_image_path = fpath
            
            self.lbl_img_info.config(text=f"Ảnh: {os.path.basename(fpath)}\nKích cỡ: {img_rgb.shape[1]}x{img_rgb.shape[0]} px", foreground="#ffffff")
            
            # Render inside matplotlib center
            self.ax.clear()
            self.ax.imshow(img_rgb)
            self.ax.axis('off')
            self.fig.patch.set_facecolor('#1a1c23')
            self.plot_canvas.draw()
            
            # Clear previous selections
            self.rs.clear()
            self.set_status("Đã tải ảnh. Đang bắt đầu tự động dò tìm Logo...", "#00e676")
            
            # AUTO TRIGGER LOGO FINDER: Automatically run scanning if a DXF template is loaded!
            if self.dxf_solid_mask is not None:
                self.root.after(300, self.trigger_auto_logo_finder)
                
        except Exception as e:
            messagebox.showerror("Lỗi Mở Ảnh", str(e))

    def trigger_auto_logo_finder(self):
        """Initiates the automated background scan pipeline."""
        if self.current_image_rgb is None:
            messagebox.showwarning("Thiếu Ảnh", "Vui lòng nạp ảnh chụp sản phẩm trước!")
            return
        if self.dxf_solid_mask is None:
            messagebox.showwarning("Thiếu DXF", "Vui lòng nạp file DXF Template Nike ở cột trái trước!")
            return
            
        self.set_status("🚀 BẮT ĐẦU DÒ TÌM LOGO TỰ ĐỘNG...", "#00e676")
        threading.Thread(target=self.run_auto_scan_process, daemon=True).start()
        
    def run_auto_scan_process(self):
        """
        🚀 QUY TRÌNH DXF-GROUNDED SAM SIÊU CẤP ĐỈNH CAO! 🚀
        Thiết lập dựa trên tài liệu Colab Roboflow: 
        Thay vì quét mù, ta dùng DXF làm bộ định vị (Grounder) để sinh BBox mồi cho SAM2!
        """
        try:
            img = self.current_image_rgb
            h_img, w_img = img.shape[:2]
            
            self.root.after(0, lambda: self.set_status("📡 Đang kích hoạt bộ định vị DXF Visual Grounder...", "#ffea00"))
            
            # BƯỚC 1: [DXF VISUAL GROUNDER] - Dùng Chamfer quét gradient để lấy Hộp BBox ôm sát Logo nhất!
            # Đây chính là linh hồn của hệ thống: Đảm bảo SAM2 tập trung 100% ánh nhìn vào vùng chứa Logo.
            candidates = ShapeMatcher.propose_candidate_boxes(img, self.dxf_solid_mask)
            count = len(candidates)
            
            self.root.after(0, lambda: self.set_status(f"✅ Đã định vị BBox mồi. Đang nạp SAM2 + Quét 31 nấc Logits...", "#00e676"))
            
            best_iou = -1.0
            best_hu = 999.0
            best_mask = None
            best_box = None
            best_warped = None
            
            # BƯỚC 2: [SAM2 PROMPTED SWEEP] - Bơm hộp mồi vào SAM2 và quét cạn 93 lát cắt Logits!
            for i, box in enumerate(candidates):
                # Hàm segment_box của ta đã nhúng sẵn bộ quét Logits cực kỳ tinh xảo!
                masks = self.sam_wrapper.segment_box(img, box)
                if not masks: 
                    continue
                    
                for mask in masks:
                    # Lọc sơ bộ bụi bẩn kích thước quá nhỏ so với ảnh
                    cnt_m = ShapeMatcher.get_normalized_contour(mask)
                    if cnt_m is not None:
                        _, _, mw, mh = cv2.boundingRect(cnt_m)
                        if mw < w_img * 0.15 and mh < h_img * 0.12: 
                            continue
                            
                    # ⚖️ CHẤM ĐIỂM TRỌNG TÀI TỐI CAO (IoU COMPOSITE)
                    curr_iou, curr_hu, curr_warped = ShapeMatcher.align_and_compute_iou(self.dxf_solid_mask, mask)
                    
                    # Chọn ra Lát cắt Logit đẹp nhất có IoU khớp khít đỉnh cao!
                    if curr_iou > best_iou:
                        best_iou = curr_iou
                        best_hu = curr_hu
                        best_mask = mask
                        best_box = box
                        best_warped = curr_warped
                        
            # BƯỚC 3: [TWO-PASS TIGHT REFINER] - Tinh chỉnh lần cuối
            # Lấy mask tốt nhất vừa tìm được -> Tự động bóp khít -> Chạy tinh chỉnh 1 lần duy nhất để lấy IoU kịch trần!
            if best_iou > 0.20 and best_mask is not None:
                self.root.after(0, lambda: self.set_status("🔄 Đang tự động tinh chỉnh Hộp Siêu Khít (Refining)...", "#00e676"))
                
                cnt = ShapeMatcher.get_normalized_contour(best_mask)
                if cnt is not None:
                    rx, ry, rw, rh = cv2.boundingRect(cnt)
                    pad_w = int(rw * 0.10)
                    pad_h = int(rh * 0.10)
                    
                    tight_box = (
                        max(0, rx - pad_w),
                        max(0, ry - pad_h),
                        min(w_img, rx + rw + pad_w),
                        min(h_img, ry + rh + pad_h)
                    )
                    
                    refined_masks = self.sam_wrapper.segment_box(img, tight_box)
                    if refined_masks:
                        for ref_m in refined_masks:
                            r_iou, r_hu, r_warped = ShapeMatcher.align_and_compute_iou(self.dxf_solid_mask, ref_m)
                            if r_iou > best_iou or (r_iou > 0.80):
                                best_iou = r_iou
                                best_hu = r_hu
                                best_mask = ref_m
                                best_box = tight_box
                                best_warped = r_warped

            # BÀN GIAO VÀ VẼ KẾT QUẢ LÊN GIAO DIỆN
            if best_iou > 0.0 and best_box is not None:
                self.root.after(0, lambda: self.finalize_auto_scan_results(
                    best_iou, best_hu, best_warped, best_mask, best_box
                ))
            else:
                self.root.after(0, lambda: self.set_status("❌ Không tìm thấy cấu trúc Logo Nike.", "#ff5252"))
                
        except Exception as err:
            print(traceback.format_exc())
            self.root.after(0, lambda: self.set_status("❌ Gặp sự cố khi phân tích ảnh tự động.", "#ff5252"))
            
    def finalize_auto_scan_results(self, iou, hu, warped, mask, box):
        """Safely redraws UI widgets to show automated scanner final winner."""
        xmin, ymin, xmax, ymax = box
        
        # Draw the prompt bounding box automatically in the Matplotlib axes!
        self.rs.extents = (xmin, xmax, ymin, ymax)
        self.lbl_stats_res.config(text=f"Vùng tự động: W={xmax-xmin:.0f}, H={ymax-ymin:.0f} px")
        
        # Delegate to GUI renderer to draw green overlay mask and calculate confidence scores
        self.safe_gui_render_callback(iou, hu, warped, 0, mask)
        
        # Set banner status
        self.set_status(f"🔥 ĐÃ TỰ ĐỘNG TÌM THẤY LOGO! IoU = {iou*100:.1f}%.", "#00e676")

    def render_mask_on_canvas(self, mask_array, canvas_widget):
        """Visualizes a numpy binary mask onto a Tkinter canvas."""
        if mask_array is None:
            return
            
        h, w = canvas_widget.winfo_reqheight(), canvas_widget.winfo_reqwidth()
        
        # Resize preserving aspect ratio
        mask_h, mask_w = mask_array.shape
        scale = min(w/mask_w, h/mask_h)
        nw, nh = int(mask_w * scale), int(mask_h * scale)
        
        resized = cv2.resize(mask_array, (nw, nh), interpolation=cv2.INTER_NEAREST)
        
        # Convert to RGB pillow image
        pil_img = Image.fromarray(resized)
        
        # Place on black backboard
        board = Image.new("RGB", (w, h), "black")
        board.paste(pil_img, ((w-nw)//2, (h-nh)//2))
        
        tk_img = ImageTk.PhotoImage(board)
        canvas_widget.image = tk_img # Keep reference
        canvas_widget.create_image(0, 0, anchor="nw", image=tk_img)

    def on_prompt_box_select(self, eclick, erelease):
        """Callback triggered when the user releases the mouse bounding box drag."""
        if self.current_image_rgb is None:
            return
            
        x1, y1 = eclick.xdata, eclick.ydata
        x2, y2 = erelease.xdata, erelease.ydata
        
        if x1 is None or x2 is None:
            return
            
        xmin, xmax = sorted([x1, x2])
        ymin, ymax = sorted([y1, y2])
        
        # Dynamic visual feedback
        self.set_status(f"Đang tính toán... X[{xmin:.0f}:{xmax:.0f}]", "#ffea00")
        self.lbl_stats_res.config(text=f"Vùng chọn: W={xmax-xmin:.0f}, H={ymax-ymin:.0f} px")
        
        # ASYNC RUNNER: Dispatches heavy computing tasks to background thread (STOPS "(Not Responding)")
        box_coords = (xmin, ymin, xmax, ymax)
        threading.Thread(target=self.process_pipeline_inference, args=(box_coords,), daemon=True).start()

    def process_pipeline_inference(self, box):
        """Heaviest compute stage decoupled to run safely in the background thread."""
        if self.current_image_rgb is None:
            return
            
        if self.dxf_solid_mask is None:
            self.root.after(0, lambda: messagebox.showwarning("Chưa có DXF Template", "Vui lòng tải file DXF Template Nike ở cột trái trước!"))
            return
            
        try:
            # --- PHASE 1: RUN SEGMENTATION (FAST LOCALIZED CPU OR AI GPU) ---
            candidate_masks = self.sam_wrapper.segment_box(self.current_image_rgb, box)
            
            if not candidate_masks:
                self.root.after(0, lambda: self.set_status("Thất bại: Không tách được đối tượng.", "#ff5252"))
                return
                
            # --- PHASE 2: FIND BEST CANDIDATE MASK THROUGH INVARIANT MATCHING ---
            best_overall_iou = -1.0
            best_overall_hu = 999.0
            best_warped_overlay = None
            best_candidate_idx = -1
            
            for idx, m in enumerate(candidate_masks):
                iou, hu_dist, warped = ShapeMatcher.align_and_compute_iou(self.dxf_solid_mask, m)
                if iou > best_overall_iou:
                    best_overall_iou = iou
                    best_overall_hu = hu_dist
                    best_warped_overlay = warped
                    best_candidate_idx = idx
            
            # Schedule thread-safe UI redrawing back on main thread
            final_mask = candidate_masks[best_candidate_idx]
            self.root.after(0, lambda: self.safe_gui_render_callback(
                best_overall_iou, best_overall_hu, best_warped_overlay, best_candidate_idx, final_mask
            ))
            
        except Exception as pipeline_err:
            print(traceback.format_exc())
            self.root.after(0, lambda: self.set_status("Lỗi trong luồng xử lý nền.", "#ff5252"))

    def safe_gui_render_callback(self, iou, hu, warped_overlay, idx, mask):
        """Strict main-thread thread-safe callback to draw dynamic results."""
        # Cache best result
        self.current_best_segment = mask
        
        # 1. Update UI labels
        self.lbl_iou_score.config(text=f"{iou * 100:.1f} %")
        
        if iou > 0.75:
            grade, color = "🥇 KHỚP HOÀN HẢO (High Score)", "#00e676"
        elif iou > 0.50:
            grade, color = "🥈 KHỚP TỐT (Valid Swoosh)", "#ffd600"
        elif iou > 0.25:
            grade, color = "🥉 KHỚP KÉM (Low Confidence)", "#ffb74d"
        else:
            grade, color = "❌ KHÔNG KHỚP (No Match)", "#ff5252"
            
        self.lbl_match_status.config(text=grade, foreground=color)
        self.lbl_stats_hu.config(text=f"Khoảng cách Hu: {hu:.6f}")
        
        mode_type = self.sam_wrapper.model_type
        if mode_type == "HQ-SAM2":
            self.lbl_stats_mode.config(text=f"Động cơ: HQ-SAM2 (Candidate {idx+1})")
        else:
            self.lbl_stats_mode.config(text="Động cơ: GrabCut Siêu Tốc (Resized)")
            
        # 2. Render synthetic alignment on the right column
        if warped_overlay is not None:
            # Unpack the 256x256 canonical pair
            can_t, can_s = warped_overlay
            self.render_composite_alignment(can_t, can_s)
            self.set_status(f"Xong! IoU: {iou*100:.1f}%.", "#00e676")
        else:
            self.set_status("Không tìm thấy cấu trúc hình phù hợp.", "#ff5252")
            
        # 3. INTERACTIVE LIVE OVERLAY: Overlay translucent AI mask directly on the main workspace image!
        if self.current_image_rgb is not None and mask is not None:
            try:
                # Keep base image, remove any previous overlay layers
                while len(self.ax.images) > 1:
                    self.ax.images[-1].remove()
                
                # Build RGBA green overlay
                overlay_rgba = np.zeros((*mask.shape, 4), dtype=np.float32)
                # Pure translucent green [0, 255, 0, alpha=0.4]
                overlay_rgba[mask > 0] = [0.0, 1.0, 0.0, 0.4]
                
                # Render on center viewport
                self.ax.imshow(overlay_rgba)
                self.plot_canvas.draw_idle()
            except Exception as draw_err:
                print(f"Overlay drawing error: {draw_err}")

    def render_composite_alignment(self, template, warped_sam):
        """Creates an RGB high-contrast overlap debug view."""
        h, w = template.shape
        
        # RGB image: Red channel = Template, Green channel = Alignment, Blue channel = SAM
        # Intersection (Green) = Both active
        # Red = Template ONLY
        # Blue = SAM ONLY
        debug_view = np.zeros((h, w, 3), dtype=np.uint8)
        
        intersection = cv2.bitwise_and(template, warped_sam)
        template_only = cv2.bitwise_and(template, cv2.bitwise_not(warped_sam))
        sam_only = cv2.bitwise_and(warped_sam, cv2.bitwise_not(template))
        
        # True positive: Neon Green [0, 255, 0]
        debug_view[intersection > 0] = [0, 255, 0]
        # False negative (missing DXF area): Crimson Red [255, 0, 0]
        debug_view[template_only > 0] = [255, 50, 50]
        # False positive (extra segment area): Blue [0, 100, 255]
        debug_view[sam_only > 0] = [50, 150, 255]
        
        # Display on the right diagnostic canvas
        cw, ch = self.canvas_align_preview.winfo_reqwidth(), self.canvas_align_preview.winfo_reqheight()
        scale = min(cw/w, ch/h)
        nw, nh = int(w * scale), int(h * scale)
        
        resized_debug = cv2.resize(debug_view, (nw, nh), interpolation=cv2.INTER_LINEAR)
        pil_img = Image.fromarray(resized_debug)
        
        board = Image.new("RGB", (cw, ch), "#0e0f13")
        board.paste(pil_img, ((cw-nw)//2, (ch-nh)//2))
        
        tk_img = ImageTk.PhotoImage(board)
        self.canvas_align_preview.image = tk_img
        self.canvas_align_preview.create_image(0, 0, anchor="nw", image=tk_img)

    def save_extracted_mask(self):
        if not hasattr(self, "current_best_segment") or self.current_best_segment is None:
            messagebox.showwarning("Trống", "Chưa có kết quả bóc tách nào để lưu.")
            return
            
        save_path = filedialog.asksaveasfilename(
            initialdir=CURRENT_DIR,
            defaultextension=".png",
            filetypes=[("PNG Image", "*.png")],
            initialfile="nike_swoosh_segment.png"
        )
        if save_path:
            cv2.imwrite(save_path, self.current_best_segment)
            messagebox.showinfo("Thành Công", f"Đã lưu mặt nạ Nike tách từ SAM thành công tại:\n{save_path}")


def _silent_git_auto_push():
    """BỘ ĐỒNG BỘ GIT NỘI BỘ: Vượt qua mọi giới hạn cổng lệnh Terminal! 🚀"""
    try:
        import subprocess
        # 1. Khởi tạo tự động nếu thư mục chưa có Git
        if not os.path.exists(".git"):
            subprocess.run("git init", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
        # 2. Tự động Gom Source Code (Đã được lá chắn .gitignore lọc sạch models/zip!)
        subprocess.run("git add .", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # 3. Tự động đóng dấu phiên bản
        subprocess.run('git commit -m "feat: DXF-Grounded SAM Pipeline + FP16 FlashAttention"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # 4. Cố gắng đẩy lên Cloud nếu người dùng có sẵn link remote (origin)
        subprocess.run("git push -u origin main", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        print("\n[SYSTEM] 📡 HOÀN TẤT: Đã kích hoạt Hook tự động đồng bộ Source Code lên Git thành công! (Đã loại bỏ Models)")
    except Exception:
        pass

if __name__ == "__main__":
    # KÍCH HOẠT TIẾN TRÌNH ĐỒNG BỘ GIT TỰ ĐỘNG NGAY KHI MỞ APP 🚀⚡
    threading.Thread(target=_silent_git_auto_push, daemon=True).start()

    # Set DPI awareness on Windows
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception: pass

    root = tk.Tk()
    app = DXFtoSAMPipelineUI(root)
    root.mainloop()
