import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";

// Design-system tokens + components (single source of truth, #1299), bundled by Vite.
import "@ds/tokens.css";
import "@ds/components.css";

import { createQueryClient } from "@/api/queryClient";
import { router } from "@/router";

const queryClient = createQueryClient();
const container = document.getElementById("root");

if (container) {
  createRoot(container).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
}
