"""Data-only RPC parameters cannot escape the server's advertised schema."""
import unittest
from jsonschema import ValidationError
from pcb_mcp_client.rpc import parameters


class RPC(unittest.TestCase):
    def test_no_argument_tool_receives_no_request_wrapper(self):
        self.assertEqual(parameters(dict(type='object',properties={},additionalProperties=False),None),{})

    def test_missing_or_extra_input_is_refused(self):
        schema=dict(type='object',required=['request'],additionalProperties=False,
                    properties=dict(request=dict(type='object',required=['query'],
                    properties=dict(query=dict(type='string')),additionalProperties=False)))
        self.assertEqual(parameters(schema,dict(query='real part')),dict(request=dict(query='real part')))
        for request in (None,dict(query='part',execute='arbitrary code')):
            with self.assertRaises(ValidationError):parameters(schema,request)


if __name__=='__main__':unittest.main()
