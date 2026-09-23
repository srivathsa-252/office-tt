import { useEffect, useState, type ReactNode } from "react";

/**
 * Renders a screen at the design's exact artboard size (e.g. 1280×720) and
 * scales it uniformly to fit the viewport, so layout and spacing match the
 * design pixel-for-pixel on any display.
 */
export function Stage({
  width,
  height,
  background,
  children,
}: {
  width: number;
  height: number;
  background: string;
  children: ReactNode;
}) {
  const [scale, setScale] = useState(1);
  useEffect(() => {
    const fit = () =>
      setScale(Math.min(window.innerWidth / width, window.innerHeight / height));
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [width, height]);

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        overflow: "hidden",
      }}
    >
      <div style={{ width: width * scale, height: height * scale, flexShrink: 0 }}>
        <div
          style={{
            width,
            height,
            transform: `scale(${scale})`,
            transformOrigin: "top left",
            position: "relative",
          }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
