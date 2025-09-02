import React from 'react';
import type { Metadata } from 'next';
import { CssBaseline } from '@mui/material';

export const metadata: Metadata = {
  title: 'Weather LSA Control',
  description: 'Next.js UI for Weather LSA Control'
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <CssBaseline />
        {children}
      </body>
    </html>
  );
}
