/** Navigation-only ListDir location exposed by Windows Wrappers. */
export const WINDOWS_DRIVES_PATH = "::drives::";

export function selectedWorkspacePath(
  manual: string, path: string | null, pending: boolean,
): string | null {
  const selected = manual.trim() || (!pending ? path : null);
  return selected && selected !== WINDOWS_DRIVES_PATH ? selected : null;
}
