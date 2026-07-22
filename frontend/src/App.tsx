import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import DashboardLayout from "@/components/layout/DashboardLayout";
import Dashboard from "@/pages/Dashboard";
import TestMatrix from "@/pages/TestMatrix";
import TestRunDetails from "@/pages/TestRunDetails";
import Schedules from "@/pages/Schedules";
import Proposals from "@/pages/Proposals";
import Environments from "@/pages/Environments";
import VisualReview from "@/pages/VisualReview";
import ApiKeys from "@/pages/ApiKeys";
import Webhooks from "@/pages/Webhooks";
import FlakyTests from "@/pages/FlakyTests";
import QualityDashboard from "@/pages/QualityDashboard";
import Monitors from "@/pages/Monitors";
import Security from "@/pages/Security";
import Triage from "@/pages/Triage";
import Analytics from "@/pages/Analytics";
import Billing from "@/pages/Billing";
import Traceability from "@/pages/Traceability";
import ImportPlaywright from "@/pages/ImportPlaywright";
import IssueTrackers from "@/pages/IssueTrackers";
import TestSuites from "@/pages/TestSuites";
import SuiteDetails from "@/pages/SuiteDetails";
import Login from "@/pages/Login";
import Signup from "@/pages/Signup";
import ForgotPassword from "@/pages/ForgotPassword";
import ResetPassword from "@/pages/ResetPassword";
import VerifyEmail from "@/pages/VerifyEmail";
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
            <Route path="/forgot-password" element={<ForgotPassword />} />
            <Route path="/reset-password" element={<ResetPassword />} />
            <Route path="/verify-email" element={<VerifyEmail />} />

            <Route element={<PrivateRoute />}>
              <Route path="/" element={<DashboardLayout />}>
                <Route index element={<Dashboard />} />
                <Route path="runs" element={<TestMatrix />} />
                <Route path="runs/:runId" element={<TestRunDetails />} />
                <Route path="suites" element={<TestSuites />} />
                <Route path="suites/:suiteId" element={<SuiteDetails />} />
                <Route path="quality" element={<QualityDashboard />} />
                <Route path="monitors" element={<Monitors />} />
                <Route path="security" element={<Security />} />
                <Route path="triage" element={<Triage />} />
                <Route path="analytics" element={<Analytics />} />
                <Route path="billing" element={<Billing />} />
                <Route path="traceability" element={<Traceability />} />
                <Route path="import-playwright" element={<ImportPlaywright />} />
                <Route path="schedules" element={<Schedules />} />
                <Route path="proposals" element={<Proposals />} />
                <Route path="environments" element={<Environments />} />
                <Route path="visual-review" element={<VisualReview />} />
                <Route path="flaky-tests" element={<FlakyTests />} />
                <Route path="api-keys" element={<ApiKeys />} />
                <Route path="webhooks" element={<Webhooks />} />
                <Route path="issue-trackers" element={<IssueTrackers />} />
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
