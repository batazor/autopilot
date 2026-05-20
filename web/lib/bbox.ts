/** Percent bbox as stored in area.json (same as Streamlit annotator). */
export type PercentBBox = {
  x: number;
  y: number;
  width: number;
  height: number;
  rotation: number;
  original_width: number;
  original_height: number;
};

export type EditorRegion = {
  id: string;
  name: string;
  action: string;
  threshold: number;
  bbox: PercentBBox;
  overlay_auxiliary?: boolean;
  has_red_dot?: boolean;
  isSearch?: boolean;
  type?: string;
};

export function pctToPixels(
  bbox: PercentBBox,
  imgW: number,
  imgH: number,
): { x: number; y: number; width: number; height: number } {
  return {
    x: (bbox.x / 100) * imgW,
    y: (bbox.y / 100) * imgH,
    width: (bbox.width / 100) * imgW,
    height: (bbox.height / 100) * imgH,
  };
}

export function pixelsToPct(
  x: number,
  y: number,
  width: number,
  height: number,
  imgW: number,
  imgH: number,
  rotation = 0,
): PercentBBox {
  if (imgW <= 0 || imgH <= 0) {
    throw new Error("image dimensions must be positive");
  }
  return {
    x: (100 * x) / imgW,
    y: (100 * y) / imgH,
    width: (100 * width) / imgW,
    height: (100 * height) / imgH,
    rotation,
    original_width: imgW,
    original_height: imgH,
  };
}
