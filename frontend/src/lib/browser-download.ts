/**
 * Triggers a browser file download for a Blob instance.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/**
 * Triggers a browser file download from a Fetch API Response.
 * Reads the response body as a Blob and delegates to `downloadBlob`.
 */
export async function downloadResponse(
  response: Response,
  filename: string,
): Promise<void> {
  const blob = await response.blob();
  downloadBlob(blob, filename);
}
