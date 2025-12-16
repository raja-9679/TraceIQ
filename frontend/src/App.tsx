import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import DashboardLayout from "@/components/layout/DashboardLayout";
import Dashboard from "@/pages/Dashboard";
import TestMatrix from "@/pages/TestMatrix";
import TestRunDetails from "@/pages/TestRunDetails";
import TestSuites from "@/pages/TestSuites";
import SuiteDetails from "@/pages/SuiteDetails";

const queryClient = new QueryClient();

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<DashboardLayout />}>
            <Route index element={<Dashboard />} />
            <Route path="runs" element={<TestMatrix />} />
            <Route path="runs/:runId" element={<TestRunDetails />} />
            <Route path="suites" element={<TestSuites />} />
            <Route path="suites/:suiteId" element={<SuiteDetails />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

export default App;
