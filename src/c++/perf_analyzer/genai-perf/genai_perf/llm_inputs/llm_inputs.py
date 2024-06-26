# Copyright 2024, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import random
from copy import deepcopy
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple

import requests
from genai_perf.constants import CNN_DAILY_MAIL, DEFAULT_INPUT_DATA_JSON, OPEN_ORCA
from genai_perf.exceptions import GenAIPerfException
from genai_perf.llm_inputs.synthetic_prompt_generator import SyntheticPromptGenerator
from requests import Response


class InputType(Enum):
    URL = auto()
    FILE = auto()
    SYNTHETIC = auto()


class OutputFormat(Enum):
    OPENAI_CHAT_COMPLETIONS = auto()
    OPENAI_COMPLETIONS = auto()
    TRTLLM = auto()
    VLLM = auto()


class LlmInputs:
    """
    A library of methods that control the generation of LLM Inputs
    """

    OUTPUT_FILENAME = DEFAULT_INPUT_DATA_JSON

    OPEN_ORCA_URL = "https://datasets-server.huggingface.co/rows?dataset=Open-Orca%2FOpenOrca&config=default&split=train"
    CNN_DAILYMAIL_URL = "https://datasets-server.huggingface.co/rows?dataset=cnn_dailymail&config=1.0.0&split=train"

    DEFAULT_STARTING_INDEX = 0
    MINIMUM_STARTING_INDEX = 0

    DEFAULT_LENGTH = 100
    MINIMUM_LENGTH = 1

    DEFAULT_TRTLLM_MAX_TOKENS = 256

    DEFAULT_RANDOM_SEED = 0
    DEFAULT_PROMPT_TOKENS_MEAN = 550
    DEFAULT_PROMPT_TOKENS_STDDEV = 0
    DEFAULT_EXPECTED_OUTPUT_TOKENS = 150
    DEFAULT_NUM_OF_OUTPUT_PROMPTS = 100

    EMPTY_JSON_IN_VLLM_PA_FORMAT = {"data": []}
    EMPTY_JSON_IN_TRTLLM_PA_FORMAT = {"data": []}
    EMPTY_JSON_IN_OPENAI_PA_FORMAT = {"data": []}

    dataset_url_map = {OPEN_ORCA: OPEN_ORCA_URL, CNN_DAILY_MAIL: CNN_DAILYMAIL_URL}

    @classmethod
    def create_llm_inputs(
        cls,
        input_type: InputType,
        output_format: OutputFormat,
        dataset_name: str = "",
        model_name: str = "",
        input_filename: str = "",
        starting_index: int = DEFAULT_STARTING_INDEX,
        length: int = DEFAULT_LENGTH,
        prompt_tokens_mean: int = DEFAULT_PROMPT_TOKENS_MEAN,
        prompt_tokens_stddev: int = DEFAULT_PROMPT_TOKENS_STDDEV,
        expected_output_tokens: int = DEFAULT_EXPECTED_OUTPUT_TOKENS,
        random_seed: int = DEFAULT_RANDOM_SEED,
        num_of_output_prompts: int = DEFAULT_NUM_OF_OUTPUT_PROMPTS,
        add_model_name: bool = False,
        add_stream: bool = False,
    ) -> Dict:
        """
        Given an input type, input format, and output type. Output a string of LLM Inputs
        (in a JSON dictionary) to a file

        Required Parameters
        -------------------
        input_type:
            Specify how the input is received
        output_format:
            Specify the output format

        Optional Parameters
        -------------------
        dataset_name:
            The name of the dataset
        model_name:
            The model name
        starting_index:
            Offset from within the list to start gathering inputs
        length:
            Number of entries to gather
        add_model_name:
            If true adds a model name field to each payload
        add_stream:
            If true adds a steam field to each payload

        Optional Synthetic Prompt Generation Parameters
        -----------------------------------------------
        prompt_tokens_mean:
            The mean length of the prompt to generate
        prompt_tokens_stddev:
            The standard deviation of the length of the prompt to generate
        expected_output_tokens:
            The number of tokens to expect in the output. This is used to
            determine the length of the prompt. The prompt will be generated such that the output
            will be approximately this many tokens.
        num_of_output_prompts:
            The number of synthetic output prompts to generate
        random_seed:
            Seed used to generate random values
        """

        LlmInputs._check_for_valid_args(
            input_type, dataset_name, starting_index, length
        )

        dataset = None
        if input_type == InputType.URL:
            dataset = LlmInputs._get_input_dataset_from_url(
                dataset_name, starting_index, length
            )
            generic_dataset_json = LlmInputs._convert_input_url_dataset_to_generic_json(
                dataset
            )
        elif input_type == InputType.SYNTHETIC:
            dataset = LlmInputs._get_input_dataset_from_synthetic(
                prompt_tokens_mean,
                prompt_tokens_stddev,
                expected_output_tokens,
                num_of_output_prompts,
                random_seed,
            )
            generic_dataset_json = (
                LlmInputs._convert_input_synthetic_dataset_to_generic_json(dataset)
            )
        else:
            raise GenAIPerfException(
                "Using a file to supply LLM Input is not supported at this time"
            )

        json_in_pa_format = LlmInputs._convert_generic_json_to_output_format(
            output_format, generic_dataset_json, add_model_name, add_stream, model_name
        )
        LlmInputs._write_json_to_file(json_in_pa_format)

        return json_in_pa_format

    @classmethod
    def _check_for_valid_args(
        cls, input_type: InputType, dataset_name: str, starting_index: int, length: int
    ) -> None:
        try:
            LlmInputs._check_for_dataset_name_if_input_type_is_url(
                input_type, dataset_name
            )
            LlmInputs._check_for_valid_starting_index(starting_index)
            LlmInputs._check_for_valid_length(length)
        except Exception as e:
            raise GenAIPerfException(e)

    @classmethod
    def _get_input_dataset_from_url(
        cls, dataset_name: str, starting_index: int, length: int
    ) -> Response:
        url = LlmInputs._resolve_url(dataset_name)
        configured_url = LlmInputs._create_configured_url(url, starting_index, length)
        dataset = LlmInputs._download_dataset(configured_url, starting_index, length)

        return dataset

    @classmethod
    def _get_input_dataset_from_synthetic(
        cls,
        prompt_tokens_mean: int,
        prompt_tokens_stddev: int,
        expected_output_tokens: int,
        num_of_output_prompts: int,
        random_seed: int,
    ) -> Dict:
        dataset_json = {}
        dataset_json["features"] = [{"name": "text_input"}]
        dataset_json["rows"] = []

        for index in range(0, num_of_output_prompts):
            synthetic_prompt, _ = LlmInputs._create_synthetic_prompt(
                prompt_tokens_mean,
                prompt_tokens_stddev,
                expected_output_tokens,
                random_seed + index,
            )
            dataset_json["rows"].append({"row": {"text_input": synthetic_prompt}})

        return dataset_json

    @classmethod
    def _resolve_url(cls, dataset_name: str) -> str:
        if dataset_name in LlmInputs.dataset_url_map:
            return LlmInputs.dataset_url_map[dataset_name]
        else:
            raise GenAIPerfException(
                f"{dataset_name} does not have a corresponding URL in the dataset_url_map."
            )

    @classmethod
    def _create_configured_url(cls, url: str, starting_index: int, length: int) -> str:
        starting_index_str = str(starting_index)
        length_str = str(length)
        configured_url = url + f"&offset={starting_index_str}&length={length_str}"

        return configured_url

    @classmethod
    def _download_dataset(cls, configured_url, starting_index, length) -> Response:
        dataset = LlmInputs._query_server(configured_url)

        return dataset

    @classmethod
    def _convert_input_url_dataset_to_generic_json(cls, dataset: Response) -> Dict:
        dataset_json = dataset.json()
        try:
            LlmInputs._check_for_error_in_json_of_dataset(dataset_json)
        except Exception as e:
            raise GenAIPerfException(e)

        generic_dataset_json = LlmInputs._convert_dataset_to_generic_input_json(
            dataset_json
        )

        return generic_dataset_json

    @classmethod
    def _convert_input_synthetic_dataset_to_generic_json(cls, dataset: Dict) -> Dict:
        generic_dataset_json = LlmInputs._convert_dataset_to_generic_input_json(dataset)

        return generic_dataset_json

    @classmethod
    def _convert_dataset_to_generic_input_json(cls, dataset_json: Dict) -> Dict:
        generic_input_json = LlmInputs._add_features_to_generic_json({}, dataset_json)
        generic_input_json = LlmInputs._add_rows_to_generic_json(
            generic_input_json, dataset_json
        )

        return generic_input_json

    @classmethod
    def _add_features_to_generic_json(
        cls, generic_input_json: Dict, dataset_json: Dict
    ) -> Dict:
        if "features" in dataset_json.keys():
            generic_input_json["features"] = []
            for feature in dataset_json["features"]:
                generic_input_json["features"].append(feature["name"])

        return generic_input_json

    @classmethod
    def _add_rows_to_generic_json(
        cls, generic_input_json: Dict, dataset_json: Dict
    ) -> Dict:
        generic_input_json["rows"] = []
        for row in dataset_json["rows"]:
            generic_input_json["rows"].append(row["row"])

        return generic_input_json

    @classmethod
    def _convert_generic_json_to_output_format(
        cls,
        output_format: OutputFormat,
        generic_dataset: Dict,
        add_model_name: bool,
        add_stream: bool,
        model_name: str = "",
    ) -> Dict:
        if output_format == OutputFormat.OPENAI_CHAT_COMPLETIONS:
            output_json = (
                LlmInputs._convert_generic_json_to_openai_chat_completions_format(
                    generic_dataset, add_model_name, add_stream, model_name
                )
            )
        elif output_format == OutputFormat.OPENAI_COMPLETIONS:
            output_json = LlmInputs._convert_generic_json_to_openai_completions_format(
                generic_dataset, add_model_name, add_stream, model_name
            )
        elif output_format == OutputFormat.VLLM:
            output_json = LlmInputs._convert_generic_json_to_vllm_format(
                generic_dataset, add_model_name, add_stream, model_name
            )
        elif output_format == OutputFormat.TRTLLM:
            output_json = LlmInputs._convert_generic_json_to_trtllm_format(
                generic_dataset, add_model_name, add_stream, model_name
            )
        else:
            raise GenAIPerfException(
                f"Output format {output_format} is not currently supported"
            )

        return output_json

    @classmethod
    def _convert_generic_json_to_openai_chat_completions_format(
        cls,
        dataset_json: Dict,
        add_model_name: bool,
        add_stream: bool,
        model_name: str = "",
    ) -> Dict:
        # TODO (TMA-1757): Implement a way to select a role for `text_input`
        (
            system_role_headers,
            user_role_headers,
            _,
        ) = LlmInputs._determine_json_feature_roles(dataset_json)
        pa_json = LlmInputs._populate_openai_chat_completions_output_json(
            dataset_json,
            system_role_headers,
            user_role_headers,
            add_model_name,
            add_stream,
            model_name,
        )

        return pa_json

    @classmethod
    def _convert_generic_json_to_openai_completions_format(
        cls,
        dataset_json: Dict,
        add_model_name: bool,
        add_stream: bool,
        model_name: str = "",
    ) -> Dict:
        (
            system_role_headers,
            user_role_headers,
            text_input_headers,
        ) = LlmInputs._determine_json_feature_roles(dataset_json)
        pa_json = LlmInputs._populate_openai_completions_output_json(
            dataset_json,
            system_role_headers,
            user_role_headers,
            text_input_headers,
            add_model_name,
            add_stream,
            model_name,
        )

        return pa_json

    @classmethod
    def _convert_generic_json_to_vllm_format(
        cls,
        dataset_json: Dict,
        add_model_name: bool,
        add_stream: bool,
        model_name: str = "",
    ) -> Dict:
        (
            system_role_headers,
            user_role_headers,
            text_input_headers,
        ) = LlmInputs._determine_json_feature_roles(dataset_json)

        pa_json = LlmInputs._populate_vllm_output_json(
            dataset_json,
            system_role_headers,
            user_role_headers,
            text_input_headers,
            add_model_name,
            add_stream,
            model_name,
        )

        return pa_json

    @classmethod
    def _convert_generic_json_to_trtllm_format(
        cls,
        dataset_json: Dict,
        add_model_name: bool,
        add_stream: bool,
        model_name: str = "",
    ) -> Dict:
        (
            system_role_headers,
            user_role_headers,
            text_input_headers,
        ) = LlmInputs._determine_json_feature_roles(dataset_json)

        pa_json = LlmInputs._populate_trtllm_output_json(
            dataset_json,
            system_role_headers,
            user_role_headers,
            text_input_headers,
            add_model_name,
            add_stream,
            model_name,
        )

        return pa_json

    @classmethod
    def _write_json_to_file(cls, json_in_pa_format: Dict):
        try:
            f = open(DEFAULT_INPUT_DATA_JSON, "w")
            f.write(json.dumps(json_in_pa_format, indent=2))
        finally:
            f.close()

    @classmethod
    def _determine_json_feature_roles(
        cls, dataset_json: Dict
    ) -> Tuple[List[str], List[str]]:
        SYSTEM_ROLE_LIST = ["system_prompt"]
        USER_ROLE_LIST = ["question", "article"]
        TEXT_INPUT_LIST = ["text_input"]

        system_role_headers, user_role_headers, text_input_headers = [], [], []
        if "features" in dataset_json.keys():
            for index, feature in enumerate(dataset_json["features"]):
                if feature in SYSTEM_ROLE_LIST:
                    system_role_headers.append(feature)
                if feature in USER_ROLE_LIST:
                    user_role_headers.append(feature)
                if feature in TEXT_INPUT_LIST:
                    user_role_headers.append(feature)

        assert (
            system_role_headers is not None
            or user_role_headers is not None
            or text_input_headers is not None
        )

        return system_role_headers, user_role_headers, text_input_headers

    @classmethod
    def _populate_openai_chat_completions_output_json(
        cls,
        dataset_json: Dict,
        system_role_headers: List[str],
        user_role_headers: List[str],
        add_model_name: bool,
        add_stream: bool,
        model_name: str = "",
    ) -> Dict:
        pa_json = LlmInputs._create_empty_openai_pa_json()

        for index, entry in enumerate(dataset_json["rows"]):
            pa_json["data"].append({"payload": []})
            pa_json["data"][index]["payload"].append({"messages": []})

            for header, content in entry.items():
                new_message = LlmInputs._create_new_openai_chat_completions_message(
                    header, system_role_headers, user_role_headers, content
                )

                pa_json = LlmInputs._add_new_message_to_json(
                    pa_json, index, new_message
                )

            pa_json = LlmInputs._add_optional_tags_to_openai_json(
                pa_json, index, add_model_name, add_stream, model_name
            )

        return pa_json

    @classmethod
    def _populate_openai_completions_output_json(
        cls,
        dataset_json: Dict,
        system_role_headers: List[str],
        user_role_headers: List[str],
        text_input_headers: List[str],
        add_model_name: bool,
        add_stream: bool,
        model_name: str = "",
    ) -> Dict:
        pa_json = LlmInputs._create_empty_openai_pa_json()

        for index, entry in enumerate(dataset_json["rows"]):
            pa_json["data"].append({"payload": []})
            pa_json["data"][index]["payload"].append({"prompt": [""]})

            for header, content in entry.items():
                new_prompt = LlmInputs._create_new_prompt(
                    header,
                    system_role_headers,
                    user_role_headers,
                    text_input_headers,
                    content,
                )

                pa_json = LlmInputs._add_new_prompt_to_json(pa_json, index, new_prompt)

            pa_json = LlmInputs._add_optional_tags_to_openai_json(
                pa_json, index, add_model_name, add_stream, model_name
            )

        return pa_json

    @classmethod
    def _populate_vllm_output_json(
        cls,
        dataset_json: Dict,
        system_role_headers: List[str],
        user_role_headers: List[str],
        text_input_headers: List[str],
        add_model_name: bool,
        add_stream: bool,
        model_name: str = "",
    ) -> Dict:
        pa_json = LlmInputs._create_empty_vllm_pa_json()

        for index, entry in enumerate(dataset_json["rows"]):
            pa_json["data"].append({"text_input": [""]})

            for header, content in entry.items():
                new_text_input = LlmInputs._create_new_text_input(
                    header,
                    system_role_headers,
                    user_role_headers,
                    text_input_headers,
                    content,
                )

                pa_json = LlmInputs._add_new_text_input_to_json(
                    pa_json, index, new_text_input
                )

            pa_json = LlmInputs._add_optional_tags_to_vllm_json(
                pa_json, index, add_model_name, add_stream, model_name
            )

        return pa_json

    @classmethod
    def _populate_trtllm_output_json(
        cls,
        dataset_json: Dict,
        system_role_headers: List[str],
        user_role_headers: List[str],
        text_input_headers: List[str],
        add_model_name: bool,
        add_stream: bool,
        model_name: str = "",
    ) -> Dict:
        pa_json = LlmInputs._create_empty_trtllm_pa_json()

        for index, entry in enumerate(dataset_json["rows"]):
            pa_json["data"].append({"text_input": [""]})

            for header, content in entry.items():
                new_text_input = LlmInputs._create_new_text_input(
                    header,
                    system_role_headers,
                    user_role_headers,
                    text_input_headers,
                    content,
                )

                pa_json = LlmInputs._add_new_text_input_to_json(
                    pa_json, index, new_text_input
                )

            pa_json = LlmInputs._add_required_tags_to_trtllm_json(pa_json, index)
            pa_json = LlmInputs._add_optional_tags_to_trtllm_json(
                pa_json, index, add_model_name, add_stream, model_name
            )

        return pa_json

    @classmethod
    def _create_empty_openai_pa_json(cls) -> Dict:
        empty_pa_json = deepcopy(LlmInputs.EMPTY_JSON_IN_OPENAI_PA_FORMAT)

        return empty_pa_json

    @classmethod
    def _create_empty_vllm_pa_json(cls) -> Dict:
        empty_pa_json = deepcopy(LlmInputs.EMPTY_JSON_IN_VLLM_PA_FORMAT)

        return empty_pa_json

    @classmethod
    def _create_empty_trtllm_pa_json(cls) -> Dict:
        empty_pa_json = deepcopy(LlmInputs.EMPTY_JSON_IN_TRTLLM_PA_FORMAT)

        return empty_pa_json

    @classmethod
    def _create_new_openai_chat_completions_message(
        cls,
        header: str,
        system_role_headers: List[str],
        user_role_headers: List[str],
        content: str,
    ) -> Optional[Dict]:
        # Do not add messages with blank content
        if not content:
            return {}

        if header in system_role_headers:
            new_message = {
                "role": "system",
                "content": content,
            }
        elif header in user_role_headers:
            new_message = {
                "role": "user",
                "content": content,
            }
        else:
            new_message = {}

        return new_message

    @classmethod
    def _create_new_prompt(
        cls,
        header: str,
        system_role_headers: List[str],
        user_role_headers: List[str],
        text_input_headers: List[str],
        content: str,
    ) -> Optional[str]:
        new_prompt = ""

        if (
            header in system_role_headers
            or header in user_role_headers
            or header in text_input_headers
        ):
            new_prompt = content

        return new_prompt

    @classmethod
    def _create_new_text_input(
        cls,
        header: str,
        system_role_headers: List[str],
        user_role_headers: List[str],
        text_input_headers: List[str],
        content: str,
    ) -> Optional[str]:
        new_text_input = ""

        if (
            header in system_role_headers
            or header in user_role_headers
            or header in text_input_headers
        ):
            new_text_input = content

        return new_text_input

    @classmethod
    def _add_new_message_to_json(
        cls, pa_json: Dict, index: int, new_message: Optional[Dict]
    ) -> Dict:
        if new_message:
            pa_json["data"][index]["payload"][0]["messages"].append(new_message)

        return pa_json

    @classmethod
    def _add_new_text_input_to_json(
        cls, pa_json: Dict, index: int, new_text_input: str
    ) -> Dict:
        if new_text_input:
            if pa_json["data"][index]["text_input"][0]:
                pa_json["data"][index]["text_input"][0] = (
                    pa_json["data"][index]["text_input"][0] + f" {new_text_input}"
                )
            else:
                pa_json["data"][index]["text_input"][0] = new_text_input

        return pa_json

    @classmethod
    def _add_new_prompt_to_json(
        cls, pa_json: Dict, index: int, new_prompt: str
    ) -> Dict:
        if new_prompt:
            if pa_json["data"][index]["payload"][0]["prompt"][0]:
                pa_json["data"][index]["payload"][0]["prompt"][0] = (
                    pa_json["data"][index]["payload"][0]["prompt"][0] + f" {new_prompt}"
                )
            else:
                pa_json["data"][index]["payload"][0]["prompt"][0] = new_prompt

        return pa_json

    @classmethod
    def _add_optional_tags_to_openai_json(
        cls,
        pa_json: Dict,
        index: int,
        add_model_name: bool,
        add_stream: bool,
        model_name: str = "",
    ) -> Dict:
        if add_model_name:
            pa_json["data"][index]["payload"][0]["model"] = model_name
        if add_stream:
            pa_json["data"][index]["payload"][0]["stream"] = True

        return pa_json

    @classmethod
    def _add_optional_tags_to_vllm_json(
        cls,
        pa_json: Dict,
        index: int,
        add_model_name: bool,
        add_stream: bool,
        model_name: str = "",
    ) -> Dict:
        if add_model_name:
            pa_json["data"][index]["model"] = model_name
        if add_stream:
            pa_json["data"][index]["stream"] = [True]

        return pa_json

    @classmethod
    def _add_optional_tags_to_trtllm_json(
        cls,
        pa_json: Dict,
        index: int,
        add_model_name: bool,
        add_stream: bool,
        model_name: str = "",
    ) -> Dict:
        if add_model_name:
            pa_json["data"][index]["model"] = model_name
        if add_stream:
            pa_json["data"][index]["stream"] = [True]

        return pa_json

    @classmethod
    def _add_required_tags_to_trtllm_json(
        cls,
        pa_json: Dict,
        index: int,
    ) -> Dict:
        pa_json["data"][index]["max_tokens"] = [LlmInputs.DEFAULT_TRTLLM_MAX_TOKENS]

        return pa_json

    @classmethod
    def _check_for_dataset_name_if_input_type_is_url(
        cls, input_type: InputType, dataset_name: str
    ) -> None:
        if input_type == InputType.URL and not dataset_name:
            raise GenAIPerfException(
                "Input type is URL, but dataset_name is not specified."
            )

    @classmethod
    def _check_for_valid_starting_index(cls, starting_index: int) -> None:
        if not isinstance(starting_index, int):
            raise GenAIPerfException(
                f"starting_index: {starting_index} must be an integer."
            )

        if starting_index < LlmInputs.MINIMUM_STARTING_INDEX:
            raise GenAIPerfException(
                f"starting_index: {starting_index} must be larger than {LlmInputs.MINIMUM_STARTING_INDEX}."
            )

    @classmethod
    def _check_for_valid_length(cls, length: int) -> None:
        if not isinstance(length, int):
            raise GenAIPerfException(f"length: {length} must be an integer.")

        if length < LlmInputs.MINIMUM_LENGTH:
            raise GenAIPerfException(
                f"starting_index: {length} must be larger than {LlmInputs.MINIMUM_LENGTH}."
            )

    @classmethod
    def _query_server(cls, configured_url: str) -> Response:
        try:
            response = requests.get(configured_url)
        except Exception as e:
            error_message = LlmInputs._create_error_message(e)
            raise GenAIPerfException(error_message)

        return response

    @classmethod
    def _create_error_message(cls, exception: Exception) -> str:
        url_str = exception.args[0].args[0]
        url_start = url_str.find("'")
        url_end = url_str.find("'", url_start + 1) + 1
        error_message = f"Invalid URL: {url_str[url_start:url_end]}"

        return error_message

    @classmethod
    def _check_for_error_in_json_of_dataset(cls, json_of_dataset: str) -> None:
        if "error" in json_of_dataset.keys():
            raise GenAIPerfException(json_of_dataset["error"])

    @classmethod
    def _create_synthetic_prompt(
        cls,
        prompt_tokens_mean: int,
        prompt_tokens_stddev: int,
        expected_output_tokens: int,
        random_seed: int,
    ) -> Tuple[str, int]:
        random.seed(random_seed)
        return SyntheticPromptGenerator.create_synthetic_prompt(
            prompt_tokens_mean, prompt_tokens_stddev, expected_output_tokens
        )
