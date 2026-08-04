/**
 * Provider marks for the landing page.
 *
 * These are simplified, self-drawn silhouettes in each vendor's brand colour —
 * not the official logo files. Swap in the real SVGs once we have permission to
 * use them; the API (24x24 viewBox, `className` passthrough) stays the same.
 */

import type { Provider } from "@/lib/types";

type MarkProps = { className?: string };

function DatadogMark({ className }: MarkProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="5" fill="#632CA6" />
      <g fill="#fff">
        <rect x="7.5" y="13" width="2.2" height="4" rx="1.1" />
        <rect x="10.9" y="10" width="2.2" height="7" rx="1.1" />
        <rect x="14.3" y="7" width="2.2" height="10" rx="1.1" />
      </g>
    </svg>
  );
}

function CloudWatchMark({ className }: MarkProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="#E7157B"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M7 18.5h9.4a3.6 3.6 0 0 0 .5-7.2 5.2 5.2 0 0 0-9.6-1.6A4.4 4.4 0 0 0 7 18.5Z" />
      <path d="M8.8 14.6 11 12.4l1.9 1.9 3.1-3.4" />
    </svg>
  );
}

function SentryMark({ className }: MarkProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="#7B51C8"
      strokeWidth="1.8"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M3.4 19.4a8.6 8.6 0 0 1 17.2 0" />
      <path d="M8.9 19.4a3.1 3.1 0 0 1 6.2 0" />
    </svg>
  );
}

function GoogleCloudMark({ className }: MarkProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path
        d="M12 2.8 20.1 7.4v9.2L12 21.2 3.9 16.6V7.4Z"
        fill="none"
        stroke="#4285F4"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="M9.4 14.6h5.6a2.1 2.1 0 0 0 .3-4.2 3 3 0 0 0-5.6-.9 2.55 2.55 0 0 0-.3 5.1Z"
        fill="#EA4335"
      />
    </svg>
  );
}

function LokiMark({ className }: MarkProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path
        d="M12 2.6c2.7 3.2.9 5.2 2.3 7 1.4 1.8 4.2 1.4 4.2 4.2a6.5 6.5 0 0 1-13 0c0-3.2 2.3-4.6 3.7-6.4S12 4.4 12 2.6Z"
        fill="#F46800"
      />
      <path
        d="M12 20.4a3 3 0 0 1-1.4-5.7c1.2-.7.8-1.8.3-2.9 2.1.6 3.7 2.4 3.9 4.6a3 3 0 0 1-2.8 4Z"
        fill="#FFB357"
      />
    </svg>
  );
}

function WebhookMark({ className }: MarkProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="#94a3b8"
      strokeWidth="1.6"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M12 8.4 8.7 14.1M12 8.4l3.3 5.7M9 17.2h6" />
      <circle cx="12" cy="6.2" r="2.4" fill="var(--bg-deep)" />
      <circle cx="6.6" cy="17.4" r="2.4" fill="var(--bg-deep)" />
      <circle cx="17.4" cy="17.4" r="2.4" fill="var(--bg-deep)" />
    </svg>
  );
}

export type ProviderCard = {
  provider: Provider;
  name: string;
  blurb: string;
  Mark: (props: MarkProps) => JSX.Element;
  /** False until the connector ships — the wizard only offers the live ones. */
  live: boolean;
};

export const PROVIDERS: ProviderCard[] = [
  {
    provider: "datadog",
    name: "Datadog",
    blurb: "Logs API, scoped by query and index",
    Mark: DatadogMark,
    live: true,
  },
  {
    provider: "cloudwatch",
    name: "CloudWatch",
    blurb: "Log groups via an IAM read-only key",
    Mark: CloudWatchMark,
    live: true,
  },
  {
    provider: "sentry",
    name: "Sentry",
    blurb: "Issues and events per project",
    Mark: SentryMark,
    live: true,
  },
  {
    provider: "gcp",
    name: "Google Cloud",
    blurb: "Cloud Logging filters",
    Mark: GoogleCloudMark,
    live: false,
  },
  {
    provider: "loki",
    name: "Grafana Loki",
    blurb: "LogQL against your Loki instance",
    Mark: LokiMark,
    live: false,
  },
  {
    provider: "webhook",
    name: "Webhook",
    blurb: "Push anything to a signed endpoint",
    Mark: WebhookMark,
    live: false,
  },
];

export function GitHubMark({ className }: MarkProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
      <path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48l-.01-1.7c-2.78.6-3.37-1.34-3.37-1.34-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.89 1.53 2.34 1.09 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.56-1.11-4.56-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.65 0 0 .84-.27 2.75 1.02a9.5 9.5 0 0 1 5 0c1.91-1.29 2.75-1.02 2.75-1.02.55 1.38.2 2.4.1 2.65.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.69-4.57 4.94.36.31.68.92.68 1.85l-.01 2.75c0 .27.18.58.69.48A10 10 0 0 0 12 2Z" />
    </svg>
  );
}

export function GoogleMark({ className }: MarkProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path
        fill="#4285F4"
        d="M21.6 12.23c0-.72-.06-1.4-.19-2.06H12v3.9h5.38a4.6 4.6 0 0 1-2 3.02v2.51h3.24c1.89-1.74 2.98-4.3 2.98-7.37Z"
      />
      <path
        fill="#34A853"
        d="M12 22c2.7 0 4.96-.9 6.62-2.4l-3.24-2.51c-.9.6-2.05.96-3.38.96-2.6 0-4.8-1.76-5.59-4.12H3.06v2.6A10 10 0 0 0 12 22Z"
      />
      <path
        fill="#FBBC05"
        d="M6.41 13.93a6 6 0 0 1 0-3.83V7.5H3.06a10 10 0 0 0 0 9l3.35-2.57Z"
      />
      <path
        fill="#EA4335"
        d="M12 5.98c1.47 0 2.79.5 3.83 1.5l2.86-2.86A10 10 0 0 0 3.06 7.5l3.35 2.6C7.2 7.74 9.4 5.98 12 5.98Z"
      />
    </svg>
  );
}
