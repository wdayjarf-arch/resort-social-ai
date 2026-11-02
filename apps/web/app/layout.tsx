import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'Resort Social AI', description: 'Unified resort social media operations dashboard' };
export default function RootLayout({children}:{children:React.ReactNode}) { return <html lang="en"><body>{children}</body></html>; }
