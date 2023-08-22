import re
from typing import List, Dict

from monitoring.monitorlib import fetch
from monitoring.monitorlib.fetch import evaluation
from monitoring.monitorlib.rid import RIDVersion
from monitoring.uss_qualifier.common_data_definitions import Severity
from monitoring.uss_qualifier.configurations.configuration import ParticipantID
from monitoring.uss_qualifier.scenarios.interuss.evaluation_scenario import (
    ReportEvaluationScenario,
)

from monitoring.uss_qualifier.resources.interuss.report import TestSuiteReportResource
from monitoring.uss_qualifier.resources.netrid import (
    NetRIDServiceProviders,
    NetRIDObserversResource,
)
from monitoring.uss_qualifier.resources.netrid.observers import RIDSystemObserver
from monitoring.uss_qualifier.resources.netrid.service_providers import (
    NetRIDServiceProvider,
)


class AggregateChecks(ReportEvaluationScenario):
    _rid_version: RIDVersion
    _service_providers: List[NetRIDServiceProvider]
    _observers: List[RIDSystemObserver]

    _queries: List[fetch.Query]
    _participants_by_base_url: Dict[str, ParticipantID] = dict()
    _queries_by_participant: Dict[ParticipantID, List[fetch.Query]]

    def __init__(
        self,
        report_resource: TestSuiteReportResource,
        service_providers: NetRIDServiceProviders,
        observers: NetRIDObserversResource,
    ):
        super().__init__(report_resource)
        self._queries = self.report.queries()
        self._service_providers = service_providers.service_providers
        self._observers = observers.observers

        # identify SPs and observers by their base URL
        self._participants_by_base_url.update(
            {sp.base_url: sp.participant_id for sp in self._service_providers}
        )
        self._participants_by_base_url.update(
            {dp.base_url: dp.participant_id for dp in self._observers}
        )

        # collect and classify queries by participant
        self._queries_by_participant = {
            participant: list()
            for participant in self._participants_by_base_url.values()
        }
        for query in self._queries:
            for base_url, participant in self._participants_by_base_url.items():
                if query.request.url.startswith(base_url):
                    self._queries_by_participant[participant].append(query)
                    break

    def run(self):
        self.begin_test_scenario()
        self.record_note("participants", str(self._participants_by_base_url))
        self.record_note("nb_queries", str(len(self._queries)))

        self.begin_test_case("Performance of Display Providers requests")

        self.begin_test_step("Performance of /display_data requests")
        self._dp_display_data_times_step()
        self.end_test_step()

        self.end_test_case()
        self.end_test_scenario()

    def _dp_display_data_times_step(self):
        """
        :return: the query durations of respectively the initial queries and the subsequent ones
        """

        pattern = re.compile(r"/display_data\?view=(-?\d+(.\d+)?,){3}-?\d+(.\d+)?")
        for participant, all_queries in self._queries_by_participant.items():

            # identify successful display_data queries
            relevant_queries: List[fetch.Query] = list()
            for query in all_queries:
                match = pattern.search(query.request.url)
                if match is not None and query.status_code == 200:
                    relevant_queries.append(query)

            if len(relevant_queries) == 0:
                # this may be a service provider
                self.record_note(
                    f"{participant}/display_data", "skipped check: no relevant queries"
                )
                continue

            # compute percentiles
            relevant_queries_by_url = evaluation.classify_query_by_url(relevant_queries)
            (
                init_durations,
                subsequent_durations,
            ) = evaluation.get_init_subsequent_queries_durations(
                self._rid_version.min_session_length_s, relevant_queries_by_url
            )
            [init_95th, init_99th] = evaluation.compute_percentiles(
                init_durations, [95, 99]
            )
            [subsequent_95th, subsequent_99th] = evaluation.compute_percentiles(
                subsequent_durations, [95, 99]
            )

            with self.check(
                "Performance of /display_data initial requests", [participant]
            ) as check:
                if init_95th > self._rid_version.dp_init_resp_percentile95_s:
                    check.record_failed(
                        summary=f"95th percentile of durations for initial DP display_data queries is higher than threshold",
                        severity=Severity.Medium,
                        participants=[participant],
                        details=f"threshold: {self._rid_version.dp_init_resp_percentile95_s}, 95th percentile: {init_95th}",
                    )
                if init_99th > self._rid_version.dp_init_resp_percentile99_s:
                    check.record_failed(
                        summary=f"99th percentile of durations for initial DP display_data queries is higher than threshold",
                        severity=Severity.Medium,
                        participants=[participant],
                        details=f"threshold: {self._rid_version.dp_init_resp_percentile99_s}, 99th percentile: {init_99th}",
                    )

            with self.check(
                "Performance of /display_data subsequent requests", [participant]
            ) as check:
                if subsequent_95th > self._rid_version.dp_data_resp_percentile95_s:
                    check.record_failed(
                        summary=f"95th percentile of durations for subsequent DP display_data queries is higher than threshold",
                        severity=Severity.Medium,
                        participants=[participant],
                        details=f"threshold: {self._rid_version.dp_data_resp_percentile95_s}, 95th percentile: {subsequent_95th}",
                    )
                if subsequent_99th > self._rid_version.dp_data_resp_percentile99_s:
                    check.record_failed(
                        summary=f"99th percentile of durations for subsequent DP display_data queries is higher than threshold",
                        severity=Severity.Medium,
                        participants=[participant],
                        details=f"threshold: {self._rid_version.dp_data_resp_percentile99_s}, 95th percentile: {subsequent_99th}",
                    )

            self.record_note(
                f"{participant}/display_data",
                f"percentiles on {len(relevant_queries)} relevant queries ({len(relevant_queries_by_url)} different URLs, {len(init_durations)} initial queries, {len(subsequent_durations)} subsequent queries): init 95th: {init_95th}; init 99th: {init_99th}; subsequent 95th: {subsequent_95th}; subsequent 99th: {subsequent_99th}",
            )