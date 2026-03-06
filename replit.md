# Gym Tracking Application (ZEUS)

## Overview

ZEUS is a high-performance Flask-based gym tracking web application designed for Arabic-speaking users. It features a mobile-first, OLED Black interface with Turquoise (#00E5FF) accents.

Key features include:
- **ZEUS AI Assistant**: Powered by Gemini 2.0 Flash for smart fitness and nutrition advice.
- **Weight Tracker**: Advanced logging with weekly organization and precision progress charts.
- **ZEUS 88 Engine**: Professional workout system builder.
- **Commitment Tracking**: Visual calendar for workout consistency.
- **Admin/Coach Dashboards**: Comprehensive user and program management.

## User Preferences

- **Theme**: OLED Black (#000000) background, Turquoise (#00E5FF) accents.
- **Language**: Arabic (RTL support).
- **UX**: Mobile-first, sticky bottom navigation, FAB for quick logging.

## System Architecture

### Backend
- **Flask** with **PostgreSQL** (SQLAlchemy).
- **Gemini 2.0 Flash** integration for AI features.
- Session-based auth with phone/password and email support for password recovery.
- Registration includes email collection.
- Forgot password flow using Resend API.

### Frontend
- **Bootstrap 5** with custom Dark Neon styling.
- **Chart.js** with precision data points (no decimation) and spline curves.
- **QR Server API** for profile sharing.

## Recent Changes (Feb 24, 2026)
- **High-Energy UI Refactor**: Updated "Record Log" and "Graph" pages to a vibrant, high-contrast theme.
- **Visual Enhancements**: Replaced dull greys with Pure White (#FFFFFF) and added Neon Cyan (#00E5FF) glows to key elements.
- **Graph Optimization**: Thicker neon pulse lines (5px-6px) and bright white grid/axis labels for maximum legibility.
- **Log Entry Glow**: Added glowing Cyan borders to workout logs for better visibility against the OLED black background.
- **Commitment Tracker Update**: Enhanced high-intensity Cyan for "تمرين" status and animated day headers.
- **Nutrition Compass Update**: Migrated all green accents to Vibrant Cyan (#00E5FF) for consistent branding.
- **Navigation Improvements**: Added "Back to Dashboard" navigation to all primary tracking and calculation pages.
- **Data Integrity**: Ensured existing user records and system modules are preserved while upgrading the visual layer.