import { lazy, type ComponentType } from "react";

/**
 * A page whose code downloads the first time it's opened.
 *
 * If the download fails, it is tried once more a moment later before the error reaches the
 * error boundary. A download cancelled because the tab is reloading or leaving the page then
 * never surfaces as an error (the page is gone before the retry), and a dropped connection
 * gets a second chance. If it still fails (for example a new version was deployed and this
 * tab asks for files that no longer exist), the boundary offers a reload.
 */
export function lazyPage<T extends ComponentType<object>>(load: () => Promise<{ default: T }>) {
  return lazy(() =>
    load().catch(
      () =>
        new Promise<{ default: T }>((resolve, reject) => {
          window.setTimeout(() => load().then(resolve, reject), 1500);
        }),
    ),
  );
}
