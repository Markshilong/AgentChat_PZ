# SPDX-FileCopyrightText: 2024-present Brandon Rose <rose.brandon.m@gmail.com>
#
# SPDX-License-Identifier: MIT
from typing import TYPE_CHECKING, Any, Dict, List, Tuple, Type
from datetime import datetime, timezone
from pathlib import Path
import asyncio
import os

import pandas as pd
from archytas.react import FailedTaskError
from archytas.tool_utils import AgentRef, LoopControllerRef, ReactContextRef, tool
from beaker_kernel.lib import BeakerAgent
from langchain_core.messages import HumanMessage

if TYPE_CHECKING:
    from beaker_kernel.kernel import BeakerKernel

import json

JSON_OUTPUT = False
PRINT_OUTPUT = True


class BdfPzAgent(BeakerAgent):
    """
    You are a helpful agent that is intended to assist users in using Palimpzest, a
    declarative system for optimizing AI workloads.

    """

    _prompt_usage_trace: dict[str, Any] | None = None

    @staticmethod
    def _stringify_output(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, pd.DataFrame):
            return value.to_string(index=False)
        return str(value)

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _get_prompt_usage_log_path(self) -> Path:
        configured_path = os.environ.get("PROMPT_USAGE_LOG_PATH", "").strip()
        if configured_path:
            path = Path(configured_path)
        else:
            run_path = os.environ.get("BEAKER_RUN_PATH", "./beaker")
            path = Path(run_path) / "prompt_usage.jsonl"
        if not path.is_absolute():
            path = Path.cwd() / path
        return path

    def _append_prompt_usage_record(self, record: dict[str, Any]) -> None:
        try:
            log_path = self._get_prompt_usage_log_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            # Never fail user-facing runs due to telemetry persistence.
            self.debug(
                event_type="prompt_usage_log_error",
                content={"error": str(exc)},
            )

    def _record_usage_step(
        self,
        token_estimate: int | None,
        usage_metadata: Any,
        computed_total_tokens: int | None,
    ) -> None:
        if self._prompt_usage_trace is None:
            return

        step_record: dict[str, Any] = {
            "timestamp": self._utc_now_iso(),
            "step_index": len(self._prompt_usage_trace.get("steps", [])) + 1,
            "token_estimate": token_estimate,
            "computed_total_tokens": computed_total_tokens,
        }
        if isinstance(usage_metadata, dict):
            step_record["usage_metadata"] = usage_metadata
            step_record["total_tokens"] = self._parse_int(usage_metadata.get("total_tokens"))
            step_record["input_tokens"] = self._parse_int(usage_metadata.get("input_tokens"))
            step_record["output_tokens"] = self._parse_int(usage_metadata.get("output_tokens"))
        else:
            step_record["usage_metadata"] = None
            step_record["total_tokens"] = computed_total_tokens
        self._prompt_usage_trace.setdefault("steps", []).append(step_record)

    @classmethod
    def _extract_tool_output(cls, result: Any) -> str:
        if result is None:
            return ""
        if isinstance(result, str):
            return result
        if not isinstance(result, dict):
            return cls._stringify_output(result)

        direct = result.get("return")
        if direct not in (None, ""):
            return cls._stringify_output(direct)

        stdout = result.get("stdout")
        if stdout not in (None, ""):
            return cls._stringify_output(stdout).strip()

        outputs = result.get("outputs") or result.get("output")
        if isinstance(outputs, list):
            text_chunks: list[str] = []
            for item in outputs:
                if isinstance(item, str):
                    text_chunks.append(item)
                    continue
                if not isinstance(item, dict):
                    text_chunks.append(cls._stringify_output(item))
                    continue

                data = item.get("data", {})
                text = (
                    item.get("text")
                    or item.get("output")
                    or data.get("text/plain")
                    or data.get("text/markdown")
                )
                if isinstance(text, list):
                    text = "".join(text)
                if text not in (None, ""):
                    text_chunks.append(cls._stringify_output(text))

            if text_chunks:
                return "\n".join(chunk.rstrip() for chunk in text_chunks).strip()

        display_data_list = result.get("display_data_list")
        if isinstance(display_data_list, list):
            text_chunks: list[str] = []
            for item in display_data_list:
                if not isinstance(item, dict):
                    text_chunks.append(cls._stringify_output(item))
                    continue
                text = item.get("text/plain") or item.get("text/markdown")
                if isinstance(text, list):
                    text = "".join(text)
                if text not in (None, ""):
                    text_chunks.append(cls._stringify_output(text))
            if text_chunks:
                return "\n".join(chunk.rstrip() for chunk in text_chunks).strip()

        stderr = result.get("stderr")
        if stderr not in (None, ""):
            return cls._stringify_output(stderr).strip()

        return ""

    async def react_async(self, query: str, react_context: dict = None) -> str:
        self._prompt_usage_trace = {
            "prompt": query,
            "started_at": self._utc_now_iso(),
            "steps": [],
        }
        error_text = None
        try:
            return await super().react_async(query, react_context)
        except Exception as exc:
            error_text = str(exc)
            raise
        finally:
            trace = self._prompt_usage_trace or {}
            steps = trace.get("steps", [])
            total_tokens = 0
            for step in steps:
                step_total = self._parse_int(step.get("total_tokens"))
                if step_total is not None:
                    total_tokens += step_total

            record = {
                "prompt": trace.get("prompt", query),
                "started_at": trace.get("started_at"),
                "finished_at": self._utc_now_iso(),
                "status": "error" if error_text else "ok",
                "error": error_text,
                "step_count": len(steps),
                "total_token_usage": total_tokens,
                "steps": steps,
            }
            self._append_prompt_usage_record(record)
            self._prompt_usage_trace = None

    async def execute(
        self,
        additional_messages: list | None = None,
        tools=None,
        auto_append_response: bool = True,
    ):
        if additional_messages is None:
            additional_messages = []

        self.steps += 1
        if self.steps > self.max_react_steps:
            raise FailedTaskError(
                f"Too many steps ({self.steps} > max_react_steps) during task.\n"
                f"Last action should have been either final_answer or fail_task. "
                f"Instead got: {self.last_tool_name}",
                tool_call_id=None,
            )

        if tools is None:
            tools = self.tools

        with self.spinner():
            records = await self.chat_history.records(auto_update_context=True)
            messages = [record.message for record in records] + additional_messages
            token_estimate = await self.chat_history.token_estimate(
                model=self.model, tools=tools
            )
            print("Token estimate for query: ", token_estimate)
            if self.verbose:
                self.debug(event_type="llm_request", content=messages)
            raw_result = await self.model.ainvoke(
                input=messages,
                temperature=self.temperature,
                agent_tools=tools,
            )
            usage_metadata = getattr(raw_result, "usage_metadata", None)
            print("Actual usage for query: ", usage_metadata)

        response_token_count = await self.model.token_estimate(
            messages=[HumanMessage(content=raw_result.content)]
        )

        if auto_append_response:
            self.chat_history.add_message(
                self.model._rectify_result(raw_result),
                token_count=response_token_count,
            )

        if isinstance(usage_metadata, dict):
            total_token_count = usage_metadata.get("total_tokens", None)
            if total_token_count > token_estimate:
                self.chat_history.base_tokens = total_token_count - token_estimate
        else:
            total_token_count = token_estimate + response_token_count

        result = self.model.process_result(raw_result)
        if self.verbose:
            self.debug(event_type="llm_response", content=result)

        self._record_usage_step(
            token_estimate=token_estimate,
            usage_metadata=usage_metadata,
            computed_total_tokens=self._parse_int(total_token_count),
        )

        def task_callback(task):
            self.post_execute_task = None

        if self.post_execute_task is None:
            task = asyncio.create_task(self.post_execute())
            task.add_done_callback(task_callback)
            self.post_execute_task = task

        return result

    async def auto_context(self):
        return """You are an assistent that is intended to assist users in using Palimpzest.
        Try to identify all of the steps needed, and all of the tools. Assume the user wants to do all of the steps at once.

        If the user asks to extract something from a set of documents, you can use Palimpzest to do this. First, generate a schema for the extraction. Then, if necessary filter the data to only include the relevant documents. Next, convert the dataset to the schema that was generated. Finally, execute the workload to extract the information from the dataset.
        You may need to use multiple tools to accomplish this, including the ability to register datasets, setting the input source, filtering datasets,
        convert datasets, generating schemas, and executing workloads.

        If the user mentions structured data, SQL, DuckDB, tables, queries, result previews, or materializing file paths from a table/query, prefer the dedicated tools instead of writing custom code. In particular:
        - use `register_sql_dataset` for DuckDB tables
        - use `register_query_dataset` for SQL queries
        - use `materialize_dataset` to turn a SQL-backed dataset plus path column into a file-backed dataset
        - use `preview_results`, `preview_catalog`, `search_catalog`, `preview_cached_schema`, `list_cached_schemas`, and `retrieve_dataset` for inspection
        - use `register_dataset` only for direct file or directory paths

        File-backed dataset registration now builds a reusable local DuckDB catalog by default. Before planning a fresh extraction, check whether the answer can come from:
        - `preview_catalog` for already indexed titles, abstracts, authors, captions, descriptions, and section-ready metadata
        - `search_catalog` for keyword/topic lookups over that catalog
        - `preview_cached_schema` / `list_cached_schemas` for schema results that were extracted and cached in earlier turns
        - if the user says "preview the dataset catalog" or asks to inspect indexed metadata for a file-backed dataset, call `preview_catalog` rather than `retrieve_dataset`

        If the user points at a folder containing files of mixed types (e.g. PDFs and images together), use `register_multimodal_dataset` instead of `register_dataset`. Then call `extract_multimodal_dataset` to run extraction once per modality with default schemas (PDF/text/HTML -> ScientificPaper, image -> Image; xls/audio are skipped). Each modality's results land in a separate DuckDB table named `<dataset>_<modality>_results`.

        For natural-language questions about a multimodal dataset, the user is asking about one modality at a time. Identify which modality the question is about (e.g. "images" -> image, "papers"/"PDFs" -> pdf, "text files" -> text). Then:
        - For factual or aggregate questions answerable from already-extracted fields (e.g. "how many images mention X", "list the titles"), call `register_query_dataset` with a SQL query against the modality's `<dataset>_<modality>_results` table, then `retrieve_dataset` to surface the rows. Or call `preview_results` with `result_sink_type="duckdb"` and `result_sink_target="<dataset>_<modality>_results"`.
        - For semantic questions whose answer is not in the extracted columns (e.g. "which images show microscopy?"), generate a question-specific schema with `create_schema`, then run a fresh extraction over the modality's source files. The modality's source folder is recorded under `registered_datasets[<dataset>]["modalities"][<kind>]["path"]` — call `register_dataset` on that path to make it usable with `set_input_dataset`, then `convert_dataset` and `execute_workload` as usual.

        Avoid `run_code` when an existing tool covers the task.

        Make sure you understand all the steps needed to complete the task. Try to run all of the steps at once.
        """

    @tool()
    async def register_dataset(self, path: str, name: str, agent: AgentRef) -> str:
        """
        This function registers a dataset with Palimpzest. It takes a path to a file or directory
        and a name for the dataset. The dataset will be registered and made available for use in
        subsequent operations.

        Args:
            path (str): The path to the file or directory to register as a dataset.
            name (str): The name to give to the registered dataset. If not explicitly set, the name of the file or directory will be used.

        Returns:
            str: A message indicating the result of the registration process.
        """

        code = agent.context.get_code("register_dataset", {"path": path, "name": name})
        response = await agent.context.evaluate(code)
        return self._extract_tool_output(response)

    @tool()
    async def register_multimodal_dataset(
        self, path: str, name: str, agent: AgentRef
    ) -> str:
        """
        Register a directory containing files of mixed modalities (e.g. PDFs + images + text)
        as a single multimodal dataset.

        Files are partitioned by extension into per-modality groups (pdf, text, html, image,
        xls, audio). Each group is symlinked into its own subdirectory so it can be treated
        as a homogeneous Palimpzest dataset. Modalities without a default schema (xls, audio)
        are recorded but skipped — they will not be extracted unless the user provides a schema
        and uses the single-modal flow on the modality's sub-path.

        If the directory contains files of only one recognized modality, the dataset is
        registered as a normal file-backed dataset instead and a warning is returned.

        After registration, call `extract_multimodal_dataset` to run extraction once per
        modality, or use `retrieve_dataset` to inspect the per-modality breakdown.

        Args:
            path (str): Path to a directory containing files of mixed types.
            name (str): The user-facing dataset name.

        Returns:
            str: A message summarizing per-modality file counts and any skipped modalities.
        """

        code = agent.context.get_code(
            "register_multimodal_dataset", {"path": path, "name": name}
        )
        response = await agent.context.evaluate(code)
        return self._extract_tool_output(response)

    @tool()
    async def extract_multimodal_dataset(
        self,
        dataset_name: str,
        policy_method: str,
        allow_code_synth: str,
        agent: AgentRef,
    ) -> str:
        """
        Run Palimpzest extraction once per modality on a registered multimodal dataset.

        Each modality is extracted with its default schema (PDF/text/HTML -> ScientificPaper,
        image -> Image) and results are written to a DuckDB table named
        `<dataset_name>_<modality>_results`. Modalities marked skipped at registration time
        are left untouched. If one modality fails, the others still run; failures are
        reported in the return message.

        After extraction, per-modal natural-language questions are answered by reading the
        relevant per-modality results table or by running a fresh question-specific
        extraction over the modality's source folder.

        Args:
            dataset_name (str): A multimodal dataset name registered via `register_multimodal_dataset`.
            policy_method (str): Either "min_cost" or "max_quality". Defaults to "max_quality".
            allow_code_synth (str): Whether to allow code synthesis. Defaults to "False".

        Returns:
            str: A summary of rows extracted per modality, plus failure and skipped notes.
        """

        code = agent.context.get_code(
            "extract_multimodal_dataset",
            {
                "dataset_name": dataset_name,
                "policy_method": policy_method,
                "allow_code_synth": allow_code_synth,
            },
        )
        response = await agent.context.evaluate(code)
        return self._extract_tool_output(response)

    @tool()
    async def register_sql_dataset(
        self,
        connection_name: str,
        table_name: str,
        dataset_name: str,
        agent: AgentRef,
    ) -> str:
        """
        Register a SQL table as a structured dataset.

        This MVP currently supports DuckDB-backed structured datasets for registration and preview.
        Structured datasets are not yet direct Palimpzest document inputs; they must be materialized
        into a file-backed input dataset before using `set_input_dataset`.
        After registering a SQL-backed dataset, use `retrieve_dataset` to preview its rows.
        If it contains file paths that should become Palimpzest input documents, use
        `materialize_dataset`.

        Args:
            connection_name (str): The registered/local connection name. Use `local_duckdb` for the MVP.
            table_name (str): The table to register as a dataset.
            dataset_name (str): The user-facing dataset name.

        Returns:
            str: A message indicating the registration result.
        """

        code = agent.context.get_code(
            "register_sql_dataset",
            {
                "connection_name": connection_name,
                "table_name": table_name,
                "dataset_name": dataset_name,
            },
        )
        response = await agent.context.evaluate(code)
        return self._extract_tool_output(response)

    @tool()
    async def register_query_dataset(
        self,
        connection_name: str,
        sql_query: str,
        dataset_name: str,
        agent: AgentRef,
    ) -> str:
        """
        Register a SQL query as a structured dataset.

        This MVP currently supports DuckDB-backed structured datasets for registration and preview.
        Structured datasets are not yet direct Palimpzest document inputs; they must be materialized
        into a file-backed input dataset before using `set_input_dataset`.
        After registering a query-backed dataset, use `retrieve_dataset` to preview its rows.
        If it contains file paths that should become Palimpzest input documents, use
        `materialize_dataset`.

        Args:
            connection_name (str): The registered/local connection name. Use `local_duckdb` for the MVP.
            sql_query (str): The SQL query that defines the dataset.
            dataset_name (str): The user-facing dataset name.

        Returns:
            str: A message indicating the registration result.
        """

        code = agent.context.get_code(
            "register_query_dataset",
            {
                "connection_name": connection_name,
                "sql_query": sql_query,
                "dataset_name": dataset_name,
            },
        )
        response = await agent.context.evaluate(code)
        return self._extract_tool_output(response)

    @tool()
    async def materialize_dataset(
        self,
        dataset_name: str,
        path_column: str,
        output_dataset_name: str,
        agent: AgentRef,
    ) -> str:
        """
        Materialize a structured SQL-backed dataset into a file-backed dataset.

        This is the MVP bridge from structured rows to the existing Palimpzest workflow.
        The tool reads a registered SQL dataset, extracts file paths from one column, creates
        a materialized directory of symlinks, and registers that directory as a normal dataset.
        Use `retrieve_dataset` first if you need to inspect the available columns or confirm that
        the chosen `path_column` contains the document paths you want to materialize.

        Args:
            dataset_name (str): The registered SQL-backed dataset to materialize.
            path_column (str): The column containing file paths.
            output_dataset_name (str): The name of the new file-backed dataset to register.

        Returns:
            str: A message indicating the materialization result.
        """

        code = agent.context.get_code(
            "materialize_dataset",
            {
                "dataset_name": dataset_name,
                "path_column": path_column,
                "output_dataset_name": output_dataset_name,
            },
        )
        response = await agent.context.evaluate(code)
        return self._extract_tool_output(response)

    @tool()
    async def unregister_dataset(self, dataset_name: str, agent: AgentRef) -> str:
        """
        This function unregisters a dataset with Palimpzest. It takes a dataset name and unregisters the dataset. The dataset will be unregistered and made
        unavailable for use in subsequent operations.

        Args:
            dataset_name (str): The name of the dataset to unregister.

        Returns:
            str: A message indicating the result of the unregistration process.
        """

        code = agent.context.get_code(
            "unregister_dataset", {"dataset_name": dataset_name}
        )
        if PRINT_OUTPUT:
            print(code)
        response = await agent.context.evaluate(code)
        return self._extract_tool_output(response)

    @tool()
    async def list_datasets(self, agent: AgentRef) -> str:
        """
        This function lists all available datasets in the system. You should use these results to nicely format the output for the user.

        Returns:
            str: A table of the datasets in the system.
        """

        code = agent.context.get_code("list_datasets", {})
        if PRINT_OUTPUT:
            print(code)

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )

            return self._extract_tool_output(result)

    @tool()
    async def retrieve_dataset(self, dataset_name: str, agent: AgentRef) -> list[str]:
        """
        This function inspects a registered dataset.

        For file-backed datasets, it returns catalog rows when the dataset has already been
        indexed; otherwise it lists the available items within the dataset path.
        For SQL-backed datasets, it previews rows from the registered table or query result.
        Use this tool after `register_sql_dataset` or `register_query_dataset` when you need
        to inspect structured rows without writing custom code. Do not use this as the first
        choice when the user explicitly asks for a catalog preview; use `preview_catalog`.

        Args:
            dataset_name (str): The name of the dataset to retrieve.

        Returns:
            list[str]: file identifiers for file-backed datasets, or a row preview for SQL-backed datasets.
        """

        code = agent.context.get_code(
            "retrieve_dataset",
            {"dataset_name": dataset_name},
        )
        if PRINT_OUTPUT:
            print(code)

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )

            return self._extract_tool_output(result)

        return ""

    @tool()
    async def create_schema(
        self,
        schema_name: str,
        field_names: list,
        field_descriptions: list,
        field_types: list,
        agent: AgentRef,
    ) -> str:
        """
        This function takes in a set of fields to be used to generate an extraction schema.
        Typically it is called when users want to extract some piece of information from a set of documents.
        After the schema is created, the input dataset should be converted to the new schema.
        This should be used when the user is interested in generating a new type of extraction schema. For example, let's say the user is interested in extracting parameter values from a set of scientific papers. The user can define the fields of the schema to be used for the extraction.
        In this case the schema name might be `Parameter` and the field information is passed in via three lists which must be constructed in proper order. For example, for parameter extractions the fields may be `name`, `value`, `unit`, `source`, etc.
        You should provide a description for each field as well as whether the type of the field (str, int, etc.). These have to be in the same order as you provide the field names. Field names should not have spaces or special characters, but can have underscores.

        Args:
            schema_name (str): the name of the schema to add
            field_names (list): a list of field names
            field_descriptions (list): a list of field descriptions
            field_types (list): a list of native Python types for the fields

        Returns:
            str: the name of the new schema that was created
        """

        code = agent.context.get_code(
            "create_schema",
            {
                "schema_name": schema_name,
                "field_names": field_names,
                "field_descriptions": field_descriptions,
                "field_types": field_types,
            },
        )
        if PRINT_OUTPUT:
            print(code)

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )

            return self._extract_tool_output(result)

    @tool()
    async def filter_data(
        self,
        input_dataset: str,
        filter_expression: str,
        agent: AgentRef,
        loop: LoopControllerRef,
    ) -> str:
        """
        This function generates a filtered dataset given an input dataset and a filtering expression. The filter expression is a string that describes a condition that has to be satisfied for each of the data item in the dataset. For example if a user is interested in a dataset of scientific papers and wants to only keep papers that are published in the year 2022, the filter expression might be "The papers is published in 2022".

        Args:
            input_dataset (str): The input Dataset to use for the filtering.
            filter_expression (str): A string that describes a condition in natural language that can be used to filter out data points within a collection.

        Returns:
            str: returns a new dataset corresponding to the filtered input dataset.
        """

        code = agent.context.get_code(
            "filter_data",
            {
                "input_dataset": input_dataset,
                "filter_expression": filter_expression,
            },
        )
        if PRINT_OUTPUT:
            print(code)

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )

            return self._extract_tool_output(result)

    @tool
    async def convert_dataset(
        self,
        input_dataset: str,
        schema_name: str,
        cardinality: str,
        agent: AgentRef,
        loop: LoopControllerRef,
    ) -> str:
        """
        This function converts an input dataset to a new output dataset with a different schema.
        The function has to be used to extract any information from a collection of input documents.
        The function is typically needed before executing a workload, to apply a generated schema to an existing dataset.
        If there is not an applicable schema, an appropriate schema should be generated using the create_schema tool.
        If multiple objects of the new schema can be extracted from a single object of the input dataset, the cardinality should be set to "one_to_many". If only one object of the new schema can be extracted from a single object of the input dataset, the cardinality should be set to "one_to_one".
        For example if a user wants to extract the titles for a dataset of scientific papers, the schema might be a TitleSchema.


        Args:
            input_dataset (str): An existing object of type dataset to use for conversion.
            schema_name (str): The name of a schema from the ones existing in the system that describes the object of the new converted dataset.
            cardinality (str): The cardinality of the conversion. Either "one_to_one" or "one_to_many".

        Returns:
            str: returns a new dataset corresponding to the converted input dataset.

        """

        code = agent.context.get_code(
            "convert_dataset",
            {
                "input_dataset": input_dataset,
                "schema_name": schema_name,
                "cardinality": cardinality,
            },
        )
        if PRINT_OUTPUT:
            print(code)

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )

            return self._extract_tool_output(result)

    @tool
    async def override_dataset(
        self, agent: AgentRef, dataset_name: str, loop: LoopControllerRef
    ) -> str:
        """
        The function is required after a workload has been executed, if the user needs to run a new workload with new converts or filters.
        The effect of this function is to reset the working dataset to the input dataset.
        This function deletes an existing dataset and sets the working dataset to a new input dataset.

        Args:
            dataset_name (str): An existing object of type dataset to use for conversion.

        Returns:
            str: returns a new dataset corresponding to the converted input dataset.

        """

        code = agent.context.get_code(
            "override_dataset",
            {
                "dataset_name": dataset_name,
            },
        )

        if PRINT_OUTPUT:
            print(code)
        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )

            return self._extract_tool_output(result)

    @tool()
    async def set_input_dataset(
        self, dataset_name: str, agent: AgentRef, loop: LoopControllerRef
    ) -> str:
        """
        This function sets the input dataset for the agent to work with when using Palimpzest (pz).
        The dataset_name is the name of the dataset, for example the name of a folder, to set as the input source.
        Often, the dataset_name is defined after registering a dataset with the appropriate tool.
        The input source, also known as the source dataset, or the input dataset, is any dataset that the user will run any workload on.
        This function should be used at the beginning of any workflow to set the input dataset for the agent to work with when using Palimpzest (pz).

        Args:
            dataset_name (str): The name of the dataset that will be set as the input source.
        Returns:
            str: returns the input source dataset as a palimpzest dataset called `dataset`.
        """

        code = agent.context.get_code(
            "set_input_dataset",
            {
                "dataset_name": dataset_name,
            },
        )
        if PRINT_OUTPUT:
            print(code)

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )
            output = self._extract_tool_output(result)
            if output == "":
                return f"Failed to set input dataset '{dataset_name}'."
            return output

    @tool()
    async def pick_schema(self, schema_name: str, agent: AgentRef) -> str:  # noqa: F821
        """
        This function picks a given schema class given its name.
        If the schema is not found, the function returns None. Provide a message to the user in this case, and proceed with creating a new schema with the given name.
        Args:
            schema_name (str): The name of the schema class to fetch.
        Returns:
            str: returns the schema class object that corresponds to the given schema name.
        """

        code = agent.context.get_code(
            "pick_schema",
            {"schema_name": schema_name},
        )

        if PRINT_OUTPUT:
            print(code)
        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )
            return self._extract_tool_output(result)

    @tool()
    async def list_schemas(self, agent: AgentRef) -> str:
        """
        This function lists all available schemas in the system. You should use these results to nicely format the output for the user.

        Returns:
            str: A table of the schemas in the system.
        """

        code = agent.context.get_code("list_schemas", {})
        if PRINT_OUTPUT:
            print(code)

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )

            return self._extract_tool_output(result)

    @tool()
    async def execute_workload(
        self,
        output_dataset: str,
        policy_method: str,
        allow_code_synth: str,
        allow_token_reduction: str,
        result_sink_type: str,
        result_sink_target: str,
        agent: AgentRef,
        loop: LoopControllerRef,
    ) -> str:
        """
        This function executes a workload starting from a given output dataset.
        If necessary, before executing the workload, any input dataset must be processed to match the schema of the output dataset.
        Processing an input dataset can be composed of several operations such as filtering or converting from one schema to the next. For example, if I want to extract the title of papers with at least 5 authors, I can first filter the papers to only include those with more than 5 authors and then convert the scientific papers to a schema that only includes the title information.
        In this case, the input dataset is the scientific papers dataset and the output dataset would be obtained first with filtering and then with converting the dataset to a schema that only includes the title information.

        The policy method chosen is either to minimize the extraction cost or to maximize the quality
        of the extraction.
        The allow_code_synth and allow_token_reduction are flags that allow the system to use optimization strategies, repsectively to run on synthesized code and to reduce the tokens used when calling LLMs.
        This returns the extractions as a Pandas DataFrame.

        Args:
            output_dataset (str): An output dataset on which to run the workload.
            policy_method (str): Either "min_cost" or "max_quality". Defaults to "max_quality".
            allow_code_synth (str): Whether to allow code synthesis or not. Defaults to "False".
            allow_token_reduction (str): Whether to allow token reduction or not. Defaults to "False".
            result_sink_type (str): Where to persist results. Supported values: "duckdb" or "csv".
            result_sink_target (str): Target table name for DuckDB or file path for CSV. If empty, a default is chosen.

        Returns:
            str: returns the extracted references as a Pandas DataFrame called `results_df`.

        You should show the user the result after this function runs.

        """

        code = agent.context.get_code(
            "execute_workload",
            {
                "output_dataset": output_dataset,
                "policy_method": policy_method,
                "allow_code_synth": allow_code_synth,
                "allow_token_reduction": allow_token_reduction,
                "result_sink_type": result_sink_type,
                "result_sink_target": result_sink_target,
            },
        )
        if PRINT_OUTPUT:
            print(code)

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )

            return self._extract_tool_output(result)

    @tool()
    async def preview_results(
        self,
        result_sink_type: str,
        result_sink_target: str,
        limit: int,
        agent: AgentRef,
    ) -> str:
        """
        Preview saved workload results from a structured sink.

        Args:
            result_sink_type (str): The sink type used to save results. Supported values: "duckdb" or "csv".
            result_sink_target (str): The DuckDB table name or CSV path to preview.
            limit (int): Maximum number of rows to preview. Defaults to 50 when using the MVP DB helpers.

        Returns:
            str: A compact preview of the saved results.
        """

        code = agent.context.get_code(
            "preview_results",
            {
                "result_sink_type": result_sink_type,
                "result_sink_target": result_sink_target,
                "limit": limit,
            },
        )

        if PRINT_OUTPUT:
            print(code)

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )
            return self._extract_tool_output(result)

    @tool()
    async def preview_catalog(
        self,
        dataset_name: str,
        modality: str,
        limit: int,
        agent: AgentRef,
    ) -> str:
        """
        Preview catalog rows that were persisted when file-backed datasets were registered.

        Args:
            dataset_name (str): Optional dataset name filter. Pass an empty string to preview across all datasets.
            modality (str): Optional modality filter such as "pdf", "text", "html", or "image".
            limit (int): Maximum number of rows to preview.

        Returns:
            str: A compact markdown preview of the stored catalog rows.
        """

        code = agent.context.get_code(
            "preview_catalog",
            {
                "dataset_name": dataset_name,
                "modality": modality,
                "limit": limit,
            },
        )

        if PRINT_OUTPUT:
            print(code)

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )
            return self._extract_tool_output(result)

    @tool()
    async def search_catalog(
        self,
        query: str,
        dataset_name: str,
        modality: str,
        limit: int,
        agent: AgentRef,
    ) -> str:
        """
        Search the persisted local catalog by keyword before planning a fresh extraction.

        Args:
            query (str): Free-text keyword or phrase to match against titles, abstracts, summaries, captions, descriptions, and topics.
            dataset_name (str): Optional dataset name filter.
            modality (str): Optional modality filter such as "pdf" or "image".
            limit (int): Maximum number of matches to return.

        Returns:
            str: A compact markdown table of matching catalog rows.
        """

        code = agent.context.get_code(
            "search_catalog",
            {
                "query": query,
                "dataset_name": dataset_name,
                "modality": modality,
                "limit": limit,
            },
        )

        if PRINT_OUTPUT:
            print(code)

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )
            return self._extract_tool_output(result)

    @tool()
    async def list_cached_schemas(
        self,
        dataset_name: str,
        agent: AgentRef,
    ) -> str:
        """
        List schema results that were cached from prior workload executions.

        Args:
            dataset_name (str): Optional dataset name filter.

        Returns:
            str: A table summarizing cached schemas, row counts, and asset counts.
        """

        code = agent.context.get_code(
            "list_cached_schemas",
            {
                "dataset_name": dataset_name,
            },
        )

        if PRINT_OUTPUT:
            print(code)

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )
            return self._extract_tool_output(result)

    @tool()
    async def preview_cached_schema(
        self,
        dataset_name: str,
        schema_name: str,
        limit: int,
        agent: AgentRef,
    ) -> str:
        """
        Preview previously cached extraction rows for a dataset/schema pair.

        Args:
            dataset_name (str): The dataset that owns the cached results.
            schema_name (str): The extracted schema name.
            limit (int): Maximum number of cached rows to preview.

        Returns:
            str: A compact markdown preview of cached schema rows.
        """

        code = agent.context.get_code(
            "preview_cached_schema",
            {
                "dataset_name": dataset_name,
                "schema_name": schema_name,
                "limit": limit,
            },
        )

        if PRINT_OUTPUT:
            print(code)

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )
            return self._extract_tool_output(result)

    @tool()
    async def print_statistics(
        self,
        agent: AgentRef,
    ) -> str:
        """
        This function shows the runtime statistics after executing a workload.
        The function can be used to check the total cost and total runtime of the pipeline that was run.
        If necessary, before showing the statistics, the workload has to be executed.

        Returns:
            str: returns the statistics objects as it is produced by the execute workflow tool.

        You should show the user the result after this function runs.

        """

        code = agent.context.get_code(
            "print_statistics",
            {},
        )

        if JSON_OUTPUT:
            return json.dumps(
                {
                    "action": "code_cell",
                    "language": "python3",
                    "content": code.strip(),
                }
            )
        else:
            result = await agent.context.evaluate(
                code,
                parent_header={},
            )
            return self._extract_tool_output(result)
