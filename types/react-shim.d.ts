/**
 * 型別檢查用的最小宣告（離線開發機沒有 @types/react 時使用；正式開發環境 npm ci 會安裝 @types/react，可刪除本檔）。
 * 只宣告本專案用到的 API，型別刻意寬鬆，目的是讓 tsc 檢查非 JSX 的邏輯錯誤。
 */
declare module 'react' {
  export type SetStateAction<S> = S | ((prev: S) => S);
  export type Dispatch<A> = (value: A) => void;
  export interface MutableRefObject<T> { current: T }
  export interface RefObject<T> { readonly current: T | null }
  export function useState<S>(initial: S | (() => S)): [S, Dispatch<SetStateAction<S>>];
  export function useEffect(effect: () => void | (() => void), deps?: readonly unknown[]): void;
  export function useLayoutEffect(effect: () => void | (() => void), deps?: readonly unknown[]): void;
  export function useRef<T>(initial: T): MutableRefObject<T>;
  export function useRef<T>(initial: T | null): RefObject<T>;
  export function useMemo<T>(fn: () => T, deps: readonly unknown[]): T;
  export function useCallback<T extends (...args: any[]) => any>(fn: T, deps: readonly unknown[]): T;
  export function useContext<T>(ctx: Context<T>): T;
  export interface Context<T> { Provider: any; Consumer: any; _t?: T }
  export function createContext<T>(v: T): Context<T>;
  export const Fragment: any;
  const React: any;
  export default React;
}
declare module 'react/jsx-runtime' {
  export const jsx: any;
  export const jsxs: any;
  export const Fragment: any;
}
declare module 'react-dom/client' {
  export function createRoot(el: Element): { render(node: any): void; unmount(): void };
}
declare module 'pdfjs-dist/legacy/build/pdf.mjs' {
  const pdfjs: any;
  export = pdfjs;
}
declare namespace JSX {
  interface IntrinsicElements { [name: string]: any }
  interface Element { }
  interface ElementAttributesProperty { props: any }
  interface IntrinsicAttributes { key?: any }
}
