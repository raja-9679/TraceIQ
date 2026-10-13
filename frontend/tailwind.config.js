const warm = {
    50: '#FBFAF7', 100: '#F3F1EC', 200: '#E8E6DF', 300: '#DCD9D0', 400: '#A8A59C',
    500: '#6B6A75', 600: '#4B4A57', 700: '#3C3B46', 800: '#2A2930', 900: '#1C1B22', 950: '#121116',
};
const inkLavender = {
    50: '#F7F6FE', 100: '#F1EFFD', 200: '#E9E7FD', 300: '#D3CFF8', 400: '#A9A2EE',
    500: '#2E2D38', 600: '#1C1B22', 700: '#0F0E13', 800: '#0B0A0E', 900: '#08070A', 950: '#040405',
};

/** @type {import('tailwindcss').Config} */
export default {
    darkMode: ["class"],
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            borderRadius: {
                lg: 'var(--radius)',
                md: 'calc(var(--radius) - 2px)',
                sm: 'calc(var(--radius) - 4px)'
            },
            fontFamily: {
                sans: ['Geist', 'system-ui', 'sans-serif'],
                mono: ['"Geist Mono"', 'ui-monospace', 'monospace'],
            },
            colors: {
                // Redesign A: one warm neutral scale replaces slate/gray/zinc,
                // and indigo/blue become ink (solids) over lavender (tints), so
                // pages not yet rebuilt still pick up the new look.
                slate: warm, gray: warm, zinc: warm, neutral: warm,
                indigo: inkLavender, blue: inkLavender,
                ink: { DEFAULT: '#1C1B22', soft: '#4B4A57', muted: '#6B6A75' },
                pastel: {
                    lavender: '#E9E7FD', pink: '#FBE4F0', peach: '#FDE8E1',
                    butter: '#FBF2D3', mint: '#DDF0EA',
                },
                'pastel-ink': {
                    lavender: '#3A31A8', pink: '#8B2560', peach: '#8A2A0C',
                    butter: '#6B4E00', mint: '#1D5E4A',
                },
                background: 'hsl(var(--background))',
                foreground: 'hsl(var(--foreground))',
                card: {
                    DEFAULT: 'hsl(var(--card))',
                    foreground: 'hsl(var(--card-foreground))'
                },
                popover: {
                    DEFAULT: 'hsl(var(--popover))',
                    foreground: 'hsl(var(--popover-foreground))'
                },
                primary: {
                    DEFAULT: 'hsl(var(--primary))',
                    foreground: 'hsl(var(--primary-foreground))'
                },
                secondary: {
                    DEFAULT: 'hsl(var(--secondary))',
                    foreground: 'hsl(var(--secondary-foreground))'
                },
                muted: {
                    DEFAULT: 'hsl(var(--muted))',
                    foreground: 'hsl(var(--muted-foreground))'
                },
                accent: {
                    DEFAULT: 'hsl(var(--accent))',
                    foreground: 'hsl(var(--accent-foreground))'
                },
                destructive: {
                    DEFAULT: 'hsl(var(--destructive))',
                    foreground: 'hsl(var(--destructive-foreground))'
                },
                border: 'hsl(var(--border))',
                input: 'hsl(var(--input))',
                ring: 'hsl(var(--ring))',
                chart: {
                    '1': 'hsl(var(--chart-1))',
                    '2': 'hsl(var(--chart-2))',
                    '3': 'hsl(var(--chart-3))',
                    '4': 'hsl(var(--chart-4))',
                    '5': 'hsl(var(--chart-5))'
                }
            }
        }
    },
    plugins: [require("tailwindcss-animate")],
}
