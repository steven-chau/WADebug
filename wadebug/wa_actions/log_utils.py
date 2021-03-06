# Copyright (c) Facebook, Inc. and its affiliates.

# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import absolute_import, division, print_function, unicode_literals

import errno
import json
import os
import shutil
from datetime import datetime, timedelta, timezone

import docker
from wadebug import exceptions
from wadebug.config import Config
from wadebug.wa_actions import docker_utils
from wadebug.wa_actions.wabiz_api import WABizAPI


CONFIG_FILE = "wadebug.conf.yml"
OUTPUT_FOLDER = "wadebug_logs"
SUPPORT_INFO_LOG_FILE = "support-info.log"
WEB_LOG_PATH = "/var/log/whatsapp"
WEB_LOG_FILE = "web.log"
WEB_ERROR_LOG_PATH = "/var/log/lighttpd"
WEB_ERROR_LOG_FILE = "error.log"

CONTAINER_LOG_DURATION_HOURS = 3
# WA container logs use UTC timezone
CONTAINER_LOG_TIMEZONE = timezone.utc


def prepare_logs(logs_since, logs_since_format):
    check_access()
    logs_start_dt, logs_end_dt = get_container_logs_start_end_datetimes(
        logs_since,
        logs_since_format,
        CONTAINER_LOG_TIMEZONE,
        CONTAINER_LOG_DURATION_HOURS,
    )

    log_files = get_logs(logs_start_dt, logs_end_dt)
    support_info_file = get_support_info()
    if support_info_file:
        log_files.append(support_info_file)
    path = os.path.join(os.getcwd(), "wadebug_logs/")
    shutil.make_archive("wadebug_logs", "zip", path)

    return open(os.path.join(os.getcwd(), "wadebug_logs.zip"), "rb"), log_files


def check_access():
    try:
        if os.access(os.getcwd(), os.R_OK):
            os.makedirs(os.path.join(os.getcwd(), OUTPUT_FOLDER))
        else:
            raise exceptions.FileAccessError(
                "Access error:  Cannot read from current directory"
            )

    except OSError as e:
        if e.errno != errno.EEXIST:
            raise exceptions.FileAccessError(
                "Access error:  Cannot write logs to current directory"
            )


def get_container_logs_start_end_datetimes(
    start_dt_str, dt_format, dt_timezone, duration_hours
):
    logs_duration = timedelta(hours=duration_hours)

    if start_dt_str:
        start_dt = datetime.strptime(start_dt_str, dt_format).replace(
            tzinfo=dt_timezone
        )
        return start_dt, start_dt + logs_duration
    else:
        end_dt = datetime.now(dt_timezone)
        return end_dt - logs_duration, end_dt


def get_logs(logs_start_dt, logs_end_dt):
    wa_containers = docker_utils.get_wa_containers()
    log_files = []
    errors = []
    for wa_container in wa_containers:
        try:
            container_log_filename = get_container_logs(
                wa_container, logs_start_dt, logs_end_dt
            )
            log_files.append(container_log_filename)
            inspect_log_filename = get_container_inspect_logs(wa_container)
            log_files.append(inspect_log_filename)
            core_dump_filename = get_corecontainer_coredumps_logs(wa_container)
            if core_dump_filename is not None:
                log_files.append(core_dump_filename)
            webapp_log, webapp_error_log = get_webcontainer_logs(wa_container)
            if webapp_log is not None and webapp_error_log is not None:
                log_files.append(webapp_log)
                log_files.append(webapp_error_log)
        except Exception as e:
            print(e)
            errors.append((wa_container.container, e))

    if errors:
        err_str = "Container: {}\nException: {}"
        exception_msg = "Some logs could not be obtained:\n{}".format(
            "\n".join([err_str.format(err[0].name, err) for err in errors])
        )
        raise exceptions.LogsNotCompleteError(exception_msg)

    return [lf for lf in log_files if lf is not None]


def get_container_logs(wa_container, logs_start_dt, logs_end_dt):
    container = wa_container.container
    container_logs = docker_utils.get_container_logs(
        container,
        # docker python SDK only accepts int
        int(logs_start_dt.timestamp()),
        int(logs_end_dt.timestamp()),
    )
    log_filename = os.path.join(
        OUTPUT_FOLDER, "{}-container.log".format(container.name)
    )
    docker_utils.write_to_file_in_binary(log_filename, container_logs)
    return log_filename


def get_container_inspect_logs(wa_container):
    container = wa_container.container
    inspect_log_filename = os.path.join(
        OUTPUT_FOLDER, "{}-inspect.log".format(container.name)
    )
    inspect_result = docker_utils.get_inspect_result(container)
    docker_utils.write_to_file(
        inspect_log_filename, json.dumps(inspect_result, indent=1)
    )
    return inspect_log_filename


def get_corecontainer_coredumps_logs(wa_container):
    container = wa_container.container
    core_dump_filename = None
    if wa_container.is_coreapp():
        core_dump_filename = os.path.join(
            OUTPUT_FOLDER, "{}-coredump.log".format(container.name)
        )
        core_dump_results = docker_utils.get_core_dump_logs(container)
        docker_utils.write_to_file(core_dump_filename, core_dump_results)
    return core_dump_filename


def get_webcontainer_logs(wa_container):
    container = wa_container.container
    webapp_log_filename = None
    webapp_error_log_filename = None
    if wa_container.is_webapp():
        webapp_log_filename = copy_additional_logs_for_webcontainer(
            container, WEB_LOG_PATH, WEB_LOG_FILE
        )
        webapp_error_log_filename = copy_additional_logs_for_webcontainer(
            container, WEB_ERROR_LOG_PATH, WEB_ERROR_LOG_PATH
        )
    return webapp_log_filename, webapp_error_log_filename


def copy_additional_logs_for_webcontainer(container, path, file_name):
    try:
        logs = docker_utils.get_archive_from_container(container, path, file_name)
        path = os.path.join(OUTPUT_FOLDER, "{}-{}".format(container.name, file_name))
        docker_utils.write_to_file(path, logs)
        return path
    except (KeyError, docker.errors.NotFound):
        pass


def get_support_info():
    support_info_filename = os.path.join(OUTPUT_FOLDER, SUPPORT_INFO_LOG_FILE)
    try:
        config = Config().values
        if config:
            api = WABizAPI(**config.get("webapp"))
            support_info_content = api.get_support_info()
        else:
            return
    except Exception:
        return

    docker_utils.write_to_file(
        support_info_filename, json.dumps(support_info_content, indent=2)
    )
    return support_info_filename
