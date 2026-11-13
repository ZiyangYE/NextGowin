module top(input clk, input a, input [7:0] b, input ce, input rst, output reg q = 1'b1);
    reg [7:0] state = 8'ha5;
    always @(posedge clk) begin
        if (rst) state <= 8'h3c;
        else if (ce) state <= state + b;
        q <= ((a & state[7]) ^ (b[0] & state[3])) |
             ((state[0] ^ b[4]) & (state[5] | b[7]));
    end
endmodule
