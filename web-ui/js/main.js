
var KrempelAirApp = angular.module('KrempelAirApp', []);
var uri = "/api"
KrempelAirApp.controller('AirFlowController', function AirFlowController($scope, $http, $timeout) {


    $scope.Refresh = function(){
        $http.get(uri+"/").then(function (response) {
            $scope.status = response.data;
        });
        $http.get(uri+"/stoerung").then(function (response) {
            $scope.stoerung = response.data;
        });
        $http.get(uri+"/lueftung/temperatur").then(function (response) {
            $scope.temperatur = response.data;
        });
        $timeout(function(){
         $scope.Refresh();
        },800);
    }
    $scope.Klick = function(Klick){
        $http.get(uri+"/lueftung/stufe/"+Klick);
        $scope.Refresh();
    }
    
    if ($scope.sollTemp) {
        $http.get(uri+"/lueftung/temperatur/"+$scope.sollTemp);
    }

    $scope.Refresh();


});
