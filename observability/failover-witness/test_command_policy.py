from command_policy import parse_operator_command


assert parse_operator_command("/usr/bin/ssh -o BatchMode=yes host command", "TEST") == (
    "/usr/bin/ssh", "-o", "BatchMode=yes", "host", "command"
)
for value in ("", "kubectl get pods", "/bin/sh -c 'echo unsafe'", "/bin/echo foo;id"):
    try:
        parse_operator_command(value, "TEST")
    except ValueError:
        pass
    else:
        raise AssertionError(f"unsafe command accepted: {value!r}")
print("test_command_policy: all assertions passed")
