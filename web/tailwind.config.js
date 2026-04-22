/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Category chips. Chosen to stay readable on both white and dark.
        urgent: "#dc2626",
        reply: "#2563eb",
        fyi: "#64748b",
        newsletter: "#7c3aed",
        trash: "#9ca3af",
      },
      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
