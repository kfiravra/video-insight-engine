import { createContext, useContext } from "react";

/**
 * The app header's slot for a page's own control (the video page's language
 * toggle): the element once Layout has mounted it, else null. A control
 * portalled there costs the page no row of its own and stays in view while
 * the page scrolls; outside a Layout (tests, bare pages) the caller renders
 * it in place.
 */
export const HeaderSlotContext = createContext<HTMLElement | null>(null);

export function useHeaderSlot(): HTMLElement | null {
  return useContext(HeaderSlotContext);
}
