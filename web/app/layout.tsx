import type { Metadata } from "next";
import "./globals.css";
import { Shell } from "@/components/Shell";

export const metadata: Metadata = {
  title: "Convertor — local conversion workspace",
  description:
    "A private, locally hosted workspace for converting files between formats.",
};

// Applied before paint so a dark-mode reload never flashes a white page.
const THEME_BOOTSTRAP = `
(function () {
  var d = document.documentElement;
  var themes = ["formal", "blush", "denim", "earth"];
  try {
    var stored = localStorage.getItem("convertor-theme");
    var mode = localStorage.getItem("convertor-mode");
    // The first release stored light/dark under the theme key; carry it over
    // rather than resetting anyone's preference.
    if (stored === "light" || stored === "dark") {
      mode = mode || stored;
      stored = "formal";
      localStorage.setItem("convertor-theme", stored);
      localStorage.setItem("convertor-mode", mode);
    }
    d.dataset.theme = themes.indexOf(stored) >= 0 ? stored : "formal";
    d.dataset.mode =
      mode === "light" || mode === "dark"
        ? mode
        : window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light";
  } catch (e) {
    d.dataset.theme = "formal";
    d.dataset.mode = "light";
  }
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body className="min-h-screen antialiased">
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
