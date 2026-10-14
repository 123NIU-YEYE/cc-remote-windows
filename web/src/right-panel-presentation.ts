import { createContext } from "react";

export const RightPanelPresentation = createContext({
  expanded: false,
  onExpand: () => {},
  onDock: () => {},
  onDismiss: () => {},
});
