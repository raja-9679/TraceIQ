import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import DashboardLayout from "@/components/layout/DashboardLayout";
import Dashboard from "@/pages/Dashboard";
import TestMatrix from "@/pages/TestMatrix";
import TestRunDetails from "@/pages/TestRunDetails";
import TestSuites from "@/pages/TestSuites";
import SuiteDetails from "@/pages/SuiteDetails";
import Login from "@/pages/Login";
import Signup from "@/pages/Signup";
import TestBuilder from "@/pages/TestBuilder";
import Settings from "@/pages/Settings";
import WorkspacePage from "@/pages/WorkspacePage";
import UsersPage from "@/pages/UsersPage";
import AdminUsersPage from "@/pages/AdminUsersPage";
import PrivateRoute from "@/components/PrivateRoute";
import { AuthProvider } from "@/context/AuthContext";

const queryClient = new QueryClient();

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/signup" element={<Signup />} />

            <Route element={<PrivateRoute />}>
              <Route path="/" element={<DashboardLayout />}>
                <Route index element={<Dashboard />} />
                <Route path="runs" element={<TestMatrix />} />
                <Route path="runs/:runId" element={<TestRunDetails />} />
                <Route path="suites" element={<TestSuites />} />
                <Route path="suites/:suiteId" element={<SuiteDetails />} />
                <Route path="suites/:suiteId/builder" element={<TestBuilder />} />
                <Route path="suites/:suiteId/cases/:caseId/edit" element={<TestBuilder />} />
                <Route path="workspace" element={<WorkspacePage />} />
                <Route path="users" element={<UsersPage />} />
                <Route path="admin/users" element={<AdminUsersPage />} />
                <Route path="settings" element={<Settings />} />
              </Route>
            </Route>

          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
