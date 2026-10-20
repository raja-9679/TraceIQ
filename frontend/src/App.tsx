import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import DashboardLayout from "@/components/layout/DashboardLayout";
import Dashboard from "@/pages/Dashboard";
import TodayInbox from "@/pages/TodayInbox";
import TestMatrix from "@/pages/TestMatrix";
import TestRunDetails from "@/pages/TestRunDetails";
import ComparisonView from "@/pages/ComparisonView";
import Schedules from "@/pages/Schedules";
import Proposals from "@/pages/Proposals";
import Environments from "@/pages/Environments";
import AppBuilds from "@/pages/AppBuilds";
import VisualReview from "@/pages/VisualReview";
import Accessibility from "@/pages/Accessibility";
import ApiKeys from "@/pages/ApiKeys";
import Webhooks from "@/pages/Webhooks";
import FlakyTests from "@/pages/FlakyTests";
import DeliveryReport from "@/pages/DeliveryReport";
import SloReports from "@/pages/SloReports";
import FlakeTrends from "@/pages/FlakeTrends";
import DependencyHealth from "@/pages/DependencyHealth";
import FrontendQuality from "@/pages/FrontendQuality";
import JourneyCoverage from "@/pages/JourneyCoverage";
import BusinessJourneys from "@/pages/BusinessJourneys";
import FlagCoverage from "@/pages/FlagCoverage";
import Benchmarks from "@/pages/Benchmarks";
import QualityDashboard from "@/pages/QualityDashboard";
import ReleaseReadiness from "@/pages/ReleaseReadiness";
import TestReview from "@/pages/TestReview";
import Monitors from "@/pages/Monitors";
import Security from "@/pages/Security";
import AccessReviews from "@/pages/AccessReviews";
import ComplianceControls from "@/pages/ComplianceControls";
import EvidencePacks from "@/pages/EvidencePacks";
import AuditLog from "@/pages/AuditLog";
import AccessMatrix from "@/pages/AccessMatrix";
import Estate from "@/pages/Estate";
import ScriptInventory from "@/pages/ScriptInventory";
import Triage from "@/pages/Triage";
import Analytics from "@/pages/Analytics";
import Billing from "@/pages/Billing";
import LLMUsage from "@/pages/LLMUsage";
import Costs from "@/pages/Costs";
import ManualCaseEditor from "@/pages/ManualCaseEditor";
import ManualRun from "@/pages/ManualRun";
import ReleaseSignoff from "@/pages/ReleaseSignoff";
import PublicStatus from "@/pages/PublicStatus";
import PublicShare from "@/pages/PublicShare";
import PublicTrust from "@/pages/PublicTrust";
import TrustCenterSettings from "@/pages/TrustCenterSettings";
import Policies from "@/pages/Policies";
import Agents from "@/pages/Agents";
import PocReport from "@/pages/PocReport";
import ValueReport from "@/pages/ValueReport";
import ExecutiveSummary from "@/pages/ExecutiveSummary";
import Portfolio from "@/pages/Portfolio";
import Traceability from "@/pages/Traceability";
import Requirements from "@/pages/Requirements";
import Incidents from "@/pages/Incidents";
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
import ProjectSettings from "@/pages/ProjectSettings";
import InstanceSettings from "@/pages/InstanceSettings";
import WorkspacePage from "@/pages/WorkspacePage";
import UsersPage from "@/pages/UsersPage";
import AdminUsersPage from "@/pages/AdminUsersPage";
import WorkerFleet from "@/pages/WorkerFleet";
import OnCall from "@/pages/OnCall";
import SizingCalculator from "@/pages/SizingCalculator";
import TestPacks from "@/pages/TestPacks";
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
            <Route path="/status/:slug" element={<PublicStatus />} />
            <Route path="/share" element={<PublicShare />} />
            <Route path="/trust/:slug" element={<PublicTrust />} />

            <Route element={<PrivateRoute />}>
              <Route path="/" element={<DashboardLayout />}>
                {/* Home is Overview (the old Dashboard) again; Inbox is kept
                    at /inbox but is not in the sidebar. */}
                <Route index element={<Navigate to="/overview" replace />} />
                <Route path="overview" element={<Dashboard />} />
                <Route path="inbox" element={<TodayInbox />} />
                <Route path="runs" element={<TestMatrix />} />
                <Route path="runs/:runId" element={<TestRunDetails />} />
                <Route path="runs/:runId/comparison" element={<ComparisonView />} />
                <Route path="suites" element={<TestSuites />} />
                <Route path="suites/:suiteId" element={<SuiteDetails />} />
                <Route path="quality" element={<QualityDashboard />} />
                <Route path="release-readiness" element={<ReleaseReadiness />} />
                <Route path="tests/review" element={<TestReview />} />
                <Route path="monitors" element={<Monitors />} />
                <Route path="security" element={<Security />} />
                <Route path="access-reviews" element={<AccessReviews />} />
                <Route path="compliance/controls" element={<ComplianceControls />} />
                <Route path="compliance/controls/:controlId" element={<ComplianceControls />} />
                <Route path="compliance/evidence-packs" element={<EvidencePacks />} />
                <Route path="audit-log" element={<AuditLog />} />
                <Route path="access-matrix" element={<AccessMatrix />} />
                <Route path="estate" element={<Estate />} />
                <Route path="script-inventory" element={<ScriptInventory />} />
                <Route path="triage" element={<Triage />} />
                <Route path="analytics" element={<Analytics />} />
                <Route path="reports/summary" element={<ExecutiveSummary />} />
                <Route path="reports/portfolio" element={<Portfolio />} />
                <Route path="reports/poc" element={<PocReport />} />
                <Route path="reports/value" element={<ValueReport />} />
                <Route path="trust-center" element={<TrustCenterSettings />} />
                <Route path="policies" element={<Policies />} />
                <Route path="agents" element={<Agents />} />
                <Route path="billing" element={<Billing />} />
                <Route path="ai-usage" element={<LLMUsage />} />
                <Route path="costs" element={<Costs />} />
                <Route path="release-signoff" element={<ReleaseSignoff />} />
                <Route path="manual-runs/:runId" element={<ManualRun />} />
                <Route path="suites/:suiteId/manual/new" element={<ManualCaseEditor />} />
                <Route path="suites/:suiteId/manual/:caseId/edit" element={<ManualCaseEditor />} />
                <Route path="traceability" element={<Traceability />} />
                <Route path="requirements" element={<Requirements />} />
                <Route path="incidents" element={<Incidents />} />
                <Route path="import-playwright" element={<ImportPlaywright />} />
                <Route path="schedules" element={<Schedules />} />
                <Route path="proposals" element={<Proposals />} />
                <Route path="environments" element={<Environments />} />
                <Route path="app-builds" element={<AppBuilds />} />
                <Route path="visual-review" element={<VisualReview />} />
                <Route path="accessibility" element={<Accessibility />} />
                <Route path="flaky-tests" element={<FlakyTests />} />
                <Route path="reports/delivery" element={<DeliveryReport />} />
                <Route path="reports/slo" element={<SloReports />} />
                <Route path="reports/flake-trends" element={<FlakeTrends />} />
                <Route path="reports/dependencies" element={<DependencyHealth />} />
                <Route path="frontend-quality" element={<FrontendQuality />} />
                <Route path="journey-coverage" element={<JourneyCoverage />} />
                <Route path="business-journeys" element={<BusinessJourneys />} />
                <Route path="flag-coverage" element={<FlagCoverage />} />
                <Route path="benchmarks" element={<Benchmarks />} />
                <Route path="api-keys" element={<ApiKeys />} />
                <Route path="webhooks" element={<Webhooks />} />
                <Route path="issue-trackers" element={<IssueTrackers />} />
                <Route path="suites/:suiteId/builder" element={<TestBuilder />} />
                <Route path="suites/:suiteId/cases/:caseId/edit" element={<TestBuilder />} />
                <Route path="workspace" element={<WorkspacePage />} />
                <Route path="users" element={<UsersPage />} />
                <Route path="admin/users" element={<AdminUsersPage />} />
                <Route path="admin/workers" element={<WorkerFleet />} />
                <Route path="on-call" element={<OnCall />} />
                <Route path="sizing" element={<SizingCalculator />} />
                <Route path="test-packs" element={<TestPacks />} />
                <Route path="settings" element={<Settings />} />
                <Route path="project-settings" element={<ProjectSettings />} />
                <Route path="instance-settings" element={<InstanceSettings />} />
              </Route>
            </Route>

          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
