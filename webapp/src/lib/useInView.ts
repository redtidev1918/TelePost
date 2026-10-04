import { useEffect, useRef, useState } from 'react';

/**
 * One-shot in-view detector used to LAZY-load media.
 *
 * Media bytes are only requested once the card actually approaches the
 * viewport, so browsing a long list never fires a request per row. When
 * IntersectionObserver is unavailable (jsdom, very old WebView) the element is
 * treated as visible immediately — the feature degrades to eager loading
 * instead of breaking.
 */
export function useInView<T extends HTMLElement>(rootMargin = '300px') {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    if (inView) return;
    const node = ref.current;
    if (!node) return;
    if (typeof IntersectionObserver === 'undefined') {
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setInView(true);
          observer.disconnect();
        }
      },
      { rootMargin },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [inView, rootMargin]);

  return { ref, inView };
}
