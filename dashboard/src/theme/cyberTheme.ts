import { createDarkTheme, type Theme } from "@fluentui/react-components";

/** 氧化铜品牌阶（亮度从深到浅），供 Fluent 计算 dark 主题衍生色。 */
const copperRamp = {
  10: "#2a1710",
  20: "#3a1f14",
  30: "#4d2a1a",
  40: "#633622",
  50: "#7d4429",
  60: "#9a5433",
  70: "#b26439",
  80: "#c2734a",
  90: "#d18a63",
  100: "#dfa37f",
  110: "#ebbd9e",
  120: "#f3d3bd",
  130: "#f8e4d5",
  140: "#fbece2",
  150: "#fdf3ec",
  160: "#ffffff",
};

export const cyberTheme: Theme = {
  ...createDarkTheme(copperRamp),
  colorNeutralBackground1: "#141c20",
  colorNeutralBackground2: "#1b252a",
  colorNeutralBackground3: "#0f1518",
  colorNeutralForeground1: "#e7e2d6",
  colorNeutralForeground2: "#a9a79c",
  colorNeutralForeground3: "#7c878b",
  colorBrandBackground: "#c2734a",
  colorBrandBackgroundHover: "#d9946b",
  colorBrandForeground1: "#d9946b",
  colorPaletteGreenForeground1: "#5f9a8b",
  colorPaletteYellowForeground1: "#d7a15c",
  colorPaletteRedForeground1: "#c25f52",
};
