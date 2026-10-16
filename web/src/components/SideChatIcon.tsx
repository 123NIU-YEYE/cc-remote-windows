export function SideChatIcon({ size = 20 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
      strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M14 3H5a3 3 0 00-3 3v6a3 3 0 003 3v4l4-4h5a3 3 0 003-3V6a3 3 0 00-3-3z" />
      <path d="M20 8a3 3 0 012 3v5a3 3 0 01-3 3v3l-4-3h-3a3 3 0 01-2.8-2" />
    </svg>
  );
}
