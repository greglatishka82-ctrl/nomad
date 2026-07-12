---
name: Nomad Drive System
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#44474e'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#75777e'
  outline-variant: '#c5c6cf'
  surface-tint: '#4e5e80'
  primary: '#031634'
  on-primary: '#ffffff'
  primary-container: '#1a2b4a'
  on-primary-container: '#8293b7'
  inverse-primary: '#b6c6ee'
  secondary: '#ab3500'
  on-secondary: '#ffffff'
  secondary-container: '#fe6a34'
  on-secondary-container: '#5d1900'
  tertiary: '#13181a'
  on-tertiary: '#ffffff'
  tertiary-container: '#282c2e'
  on-tertiary-container: '#8f9396'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#d8e2ff'
  primary-fixed-dim: '#b6c6ee'
  on-primary-fixed: '#081b39'
  on-primary-fixed-variant: '#364767'
  secondary-fixed: '#ffdbd0'
  secondary-fixed-dim: '#ffb59d'
  on-secondary-fixed: '#390c00'
  on-secondary-fixed-variant: '#832600'
  tertiary-fixed: '#e0e3e6'
  tertiary-fixed-dim: '#c3c7ca'
  on-tertiary-fixed: '#181c1e'
  on-tertiary-fixed-variant: '#43474a'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  headline-lg:
    fontFamily: Roboto
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.5px
  headline-lg-mobile:
    fontFamily: Roboto
    fontSize: 26px
    fontWeight: '700'
    lineHeight: 32px
  headline-md:
    fontFamily: Roboto
    fontSize: 24px
    fontWeight: '700'
    lineHeight: 32px
  title-lg:
    fontFamily: Roboto
    fontSize: 20px
    fontWeight: '500'
    lineHeight: 28px
  body-lg:
    fontFamily: Roboto
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Roboto
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-lg:
    fontFamily: Roboto
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
    letterSpacing: 0.1px
  label-sm:
    fontFamily: Roboto
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.5px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  container-margin: 16px
  stack-gap: 12px
  section-gap: 24px
  inline-gutter: 16px
  touch-target: 48px
---

## Brand & Style

The design system is built on the pillars of **Professionalism, Reliability, and Progress**. As a driving academy, the UI must instill confidence in students while maintaining an energetic, modern momentum. 

The aesthetic follows a **Modern Corporate** direction—a refined evolution of Material Design 3. It utilizes high-clarity layouts, purposeful whitespace, and a sophisticated color balance to ensure that complex information (like lesson schedules and traffic rules) remains digestible and stress-free. The emotional response is one of "Guided Mastery"—the interface feels like a steady hand on the wheel.

**Design Principles:**
- **Clarity First:** No decorative elements without functional purpose.
- **Directional UI:** Use of the accent color to pull the eye toward the "Next Step."
- **Institutional Trust:** Deep navy tones provide the weight of an established academy.

## Colors

The palette is anchored by **Dark Navy (#1A2B4A)**, representing authority and depth. This color is used for headers, primary actions, and key navigation elements.

**Bright Orange (#FF6B35)** serves as the high-visibility accent, reserved strictly for primary Call-to-Action (CTA) buttons, active progress states, and critical highlights. This mimicry of road signage ensures high affordance.

The background uses a tiered system of **White (#FFFFFF)** for high-elevation cards and **Neutral Gray (#F4F7FA)** for the base canvas to reduce eye strain during long study sessions. Success and error states follow high-legibility standards to ensure clear feedback during mock exams and booking confirmations.

## Typography

This design system utilizes **Roboto** for its mechanical yet friendly geometric forms, which perform exceptionally well in both Russian (Cyrillic) and Latin scripts. 

Hierarchy is established through weight rather than extreme size shifts. Headlines are set in **Bold (700)** to provide strong anchors on the page. Body text uses a generous line height (1.5x) to ensure readability for educational content. Label styles are used for "metadata" like lesson times and instructor names, often paired with icons to enhance scanning speed.

## Layout & Spacing

The layout follows a **Fluid Grid** approach optimized for mobile viewports. A standard 4-column grid is used for internal card structures, while the outer container maintains a consistent 16px margin.

**Spacing Rhythm:**
- **Vertical Stack:** Use 12px for related items within a card and 24px to separate distinct content sections.
- **Touch Targets:** All interactive elements (buttons, selectors) must maintain a minimum height of 48px to accommodate one-handed mobile use.
- **Booking Wizard:** Specifically uses a "Safe Area" bottom-docked button container for primary actions, ensuring the "Continue" button is always within thumb-reach.

## Elevation & Depth

Elevation in this design system is used to signify "interactability." We employ **Tonal Layers** combined with **Soft Ambient Shadows** to distinguish foreground tasks from the background.

- **Level 0 (Surface):** The base app background (#F4F7FA).
- **Level 1 (Cards):** White surfaces with a very soft, 10% opacity navy shadow (0px 2px 8px). Used for list items and secondary info.
- **Level 2 (Active/Modal):** White surfaces with a more pronounced 15% opacity shadow (0px 4px 16px). Used for bottom sheets and the booking wizard steps.
- **Zero-Shadow State:** Form inputs and progress tracks are inset or flat with a subtle 1px border (#E2E8F0) to avoid visual clutter.

## Shapes

The design system adopts a **Rounded** shape language to soften the "institutional" feel of the dark navy and create an approachable environment.

- **Standard Containers:** Cards and input fields use a **12px (0.75rem)** corner radius.
- **Primary Buttons:** Use a **16px (1rem)** radius to differentiate them from static cards.
- **Progress Indicators:** Use fully rounded (pill-shaped) caps for track bars to signify smooth movement and completion.

## Components

### Buttons
- **Primary:** Bright Orange background, White text, Bold weight. High-elevation shadow on tap.
- **Secondary:** Dark Navy border (2px), Navy text, Transparent background. Used for "Cancel" or "Back" actions.

### Cards & Lists
- Instructor cards feature a circular profile photo (48x48px) on the left, with Name (Title-LG) and Rating (Label-SM with an orange star) stacked to the right.
- List items have a subtle chevron icon on the right to indicate drill-down capability.

### Booking Wizard & Progress
- **Progress Bar:** A thin track at the very top of the screen. Filled segment is Bright Orange; unfilled is Light Gray.
- **Step Indicators:** "Шаг 1 из 4" (Step 1 of 4) text in Label-LG weight, placed above the main headline.

### Input Fields
- Floating label style. On focus, the border transitions from Light Gray to Dark Navy, and the label shrinks to the top border.
- Validation states: Error borders use #EF4444 with a small helper text below the field.

### Progress Indicators (Course Progress)
- **Circular Progress:** Used for "Theory Completion" percentages. Use a thick stroke (4px) with the Primary Navy for the track and Bright Orange for the progress.